"""章节知识图谱只读投影（chapter-graph-v1，合同 §6/§7/§11/§13）。

本模块把 F5 学科结构、F2 知识点绑定、知识点先修关系与现有学习记录
确定性地投影为可重建的 ``ChapterGraphSnapshot``。它是投影，不是新的
学习事实权威：绝不反向写库，绝不修改 subject_catalog、subject_structure_*、
knowledge_topic_registry、knowledge_prerequisites、学习记录或掌握度模型。

安全边界（合同 §11）：
- 零写入：本模块只使用 ``connect_readonly`` 与只读仓储读取；不出现
  ``connect()`` 写连接，不含任何 CREATE/INSERT/UPDATE/DELETE（零 DDL）。
- 提示注入：学科名、章节名、知识点名、教材/OCR 文本、用户备注与先修
  source_json 全部只是普通数据，仅用于确定性名称匹配与展示，永远不会被
  解释为指令；本模块不含任何 LLM/外部调用。

确定性聚合规则（chapter-graph-v1，合同 §7.2/§7.3 的落地定义）：

覆盖证据（coverage）：
- 学习记录满足：``date <= as_of_date``、记录学科名经 ``normalize_alias``
  归一化后属于目标学科（canonical 名或任一别名）；
- 命中绑定知识点 T：记录 ``topic`` 归一化后等于 T 的名称，或该记录任一
  ``problems[*].related_topics`` 归一化后包含 T 的名称；
- 命中的记录为 T 计 1 次覆盖（每条记录对每个知识点最多计 1 次）。

评估观察（掌握证据）：
- 直接命中（记录 topic 字段命中）：记录 ``score`` 为有限数值且 0-100，
  贡献观察值 ``score / 100``；
- 问题级命中（related_topics 命中的 problem）：``partial_credit`` 或
  ``correctness`` 为有限数值且在 0-1，贡献该值；
- 非有限（NaN/inf）或越界的数值一律排除并计入节点诊断
  ``mastery_invalid_evidence_excluded:<n>``；
- 知识点掌握值使用 Beta(2, 2) 先验对有限评估观察做贝叶斯收缩：
  ``(2 + sum(observation)) / (4 + n)``。少量全对不会直接显示 100%；
  没有评估观察 = 无掌握证据（绝不把未知伪装成 0）。

学习状态（§7.2）：
- 未绑定结构知识点或零覆盖 → ``unlearned``；
- 部分覆盖 → ``partial``；
- 全覆盖且无任何评估观察 → ``learned_unassessed``（掌握度待评估）；
- 全覆盖且 ≥1 个知识点有评估观察 → ``learned_assessed``；
- 归档学科在 ``include_archived=True`` 时节点状态改为 ``archived``，
  并保留计算前状态于 ``archived_computed_state:<state>`` 诊断。

章节掌握度聚合（§7.3）：
- 参与聚合的知识点 = 有评估观察的已覆盖知识点；权重 = 当前结构
  ``importance_bp``；部分缺失时用已有 bp 的均值补齐；全部缺失或权重
  总和 ≤ 0 时回退为等权。只要存在有效评估即输出掌握估计；
  部分覆盖章节的估计在 UI 明确标为“暂估”；
- ``evidence_confidence`` = 知识点贝叶斯证据比 ``n/(n+4)`` 的均值
  再乘以已评估知识点占本章全部知识点的比例，避免把局部证据
  伪装成高置信度；无参与知识点时为 0.0；
- ``mastery_interval`` 复用现有 ``topic_insights.mastery_interval``
  （mastery-interval-v1 规则）；不可用时为 ``None``；
- 全部输出数值经 ``math.isfinite`` 守卫。
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

from study_app.core.subject_catalog import CatalogModule, CatalogSnapshot, load_catalog_snapshot
from study_app.core.subject_identity import normalize_alias, validate_subject_key
from study_app.core.topic_insights import mastery_interval as existing_mastery_interval
from study_app.data.database import (
    DEFAULT_DB_PATH,
    DatabaseNotInitializedError,
    connect_readonly,
    load_raw_records,
    load_raw_records_from_connection,
    validate_calendar_date,
)
from study_app.data.subject_repository import SubjectLifecycleNotInstalledError


GRAPH_SCHEMA_VERSION = "chapter-graph-v1"

LEARNING_STATES = (
    "unlearned",
    "partial",
    "learned_unassessed",
    "learned_assessed",
    "archived",
)
RELATION_TYPES = ("strong_prerequisite", "supporting_relation")

# 快照级诊断（§13 降级目录）。
STRUCTURE_MISSING = "structure_missing"
STRUCTURE_VERSION_MISSING = "structure_version_missing"
STRUCTURE_EMPTY = "structure_empty"
SUBJECT_ARCHIVED = "subject_archived"
SUBJECT_UNKNOWN = "subject_unknown"
REGISTRY_MISSING = "registry_missing"
PREREQUISITE_ENDPOINT_UNMAPPED = "prerequisite_endpoint_unmapped"
PREREQUISITE_SELF_LOOP_DROPPED = "prerequisite_self_loop_dropped"
RELATIONS_MISSING = "relations_missing"
RELATIONS_CYCLE_PREFIX = "relations_cycle:"
DB_READ_FAILED = "db_read_failed"

# 节点级诊断。
NODE_STRUCTURE_TOPICS_UNBOUND = "structure_topics_unbound"
NODE_ARCHIVED_READONLY = "archived_readonly"
NODE_ARCHIVED_COMPUTED_STATE_PREFIX = "archived_computed_state:"
NODE_ORDER_CONFLICT = "order_conflict"
NODE_MASTERY_TOPICS_EXCLUDED_PREFIX = "mastery_topics_excluded:"
NODE_MASTERY_INVALID_EVIDENCE_PREFIX = "mastery_invalid_evidence_excluded:"

# Beta(2, 2) 中性先验：不会因一两次全对就显示 100%。
MASTERY_PRIOR_ALPHA = 2.0
MASTERY_PRIOR_BETA = 2.0
MASTERY_PRIOR_STRENGTH = MASTERY_PRIOR_ALPHA + MASTERY_PRIOR_BETA

_NODE_DICT_KEYS = frozenset(
    {
        "module_key",
        "display_name",
        "module_order",
        "topological_rank",
        "learning_state",
        "mastery_point",
        "mastery_interval",
        "coverage_count",
        "topic_count",
        "evidence_confidence",
        "diagnostics",
    }
)
_EDGE_DICT_KEYS = frozenset(
    {
        "from_module_key",
        "to_module_key",
        "relation_type",
        "source",
        "evidence_refs",
        "diagnostics",
    }
)
_SNAPSHOT_DICT_KEYS = frozenset(
    {
        "schema_version",
        "subject_key",
        "structure_version",
        "catalog_revision",
        "as_of_date",
        "nodes",
        "edges",
        "diagnostics",
    }
)


@dataclass(frozen=True)
class ChapterNode:
    """一个正式章节（module_key）的图节点投影。"""

    module_key: str
    display_name: str
    module_order: int
    topological_rank: int
    learning_state: str  # unlearned|partial|learned_unassessed|learned_assessed|archived
    mastery_point: float | None
    mastery_interval: tuple[float, float] | None
    coverage_count: int
    topic_count: int
    evidence_confidence: float
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "module_key": self.module_key,
            "display_name": self.display_name,
            "module_order": self.module_order,
            "topological_rank": self.topological_rank,
            "learning_state": self.learning_state,
            "mastery_point": None if self.mastery_point is None else float(self.mastery_point),
            "mastery_interval": (
                None
                if self.mastery_interval is None
                else [float(self.mastery_interval[0]), float(self.mastery_interval[1])]
            ),
            "coverage_count": self.coverage_count,
            "topic_count": self.topic_count,
            "evidence_confidence": float(self.evidence_confidence),
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class ChapterEdge:
    """一条章节关系边。v1 只有 strong_prerequisite 会实际产生。"""

    from_module_key: str
    to_module_key: str
    relation_type: str  # strong_prerequisite|supporting_relation
    source: str
    evidence_refs: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "from_module_key": self.from_module_key,
            "to_module_key": self.to_module_key,
            "relation_type": self.relation_type,
            "source": self.source,
            "evidence_refs": list(self.evidence_refs),
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class ChapterGraphSnapshot:
    """版本化只读投影（chapter-graph-v1）；可用 to_dict/from_dict 往返。"""

    schema_version: str
    subject_key: str
    structure_version: str | None
    catalog_revision: int | None
    as_of_date: str
    nodes: tuple[ChapterNode, ...]
    edges: tuple[ChapterEdge, ...]
    diagnostics: tuple[str, ...] = ()

    @property
    def relations_present(self) -> bool:
        """是否存在至少一条强先修边。"""
        return any(edge.relation_type == "strong_prerequisite" for edge in self.edges)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "subject_key": self.subject_key,
            "structure_version": self.structure_version,
            "catalog_revision": self.catalog_revision,
            "as_of_date": self.as_of_date,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "diagnostics": list(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ChapterGraphSnapshot":
        """严格校验的反序列化；缺字段/多字段/版本不兼容/非法值一律 ValueError。"""
        if not isinstance(payload, dict):
            raise ValueError("快照必须是 JSON 对象")
        missing = sorted(_SNAPSHOT_DICT_KEYS - payload.keys())
        extra = sorted(payload.keys() - _SNAPSHOT_DICT_KEYS)
        if missing:
            raise ValueError("快照缺少字段：" + "、".join(missing))
        if extra:
            raise ValueError("快照包含未知字段：" + "、".join(extra))
        if payload["schema_version"] != GRAPH_SCHEMA_VERSION:
            raise ValueError(
                f"快照版本不兼容：期望 {GRAPH_SCHEMA_VERSION}，实际 {payload['schema_version']!r}"
            )
        subject_key = _require_non_empty_str(payload["subject_key"], "subject_key")
        structure_version = payload["structure_version"]
        if structure_version is not None:
            structure_version = _require_non_empty_str(structure_version, "structure_version")
        catalog_revision = payload["catalog_revision"]
        if catalog_revision is not None:
            catalog_revision = _require_index(catalog_revision, "catalog_revision")
        as_of_date = validate_calendar_date(payload["as_of_date"], label="as_of_date")
        if not isinstance(payload["nodes"], list):
            raise ValueError("nodes 必须是数组")
        if not isinstance(payload["edges"], list):
            raise ValueError("edges 必须是数组")
        diagnostics = _require_str_list(payload["diagnostics"], "diagnostics")
        nodes = tuple(_node_from_dict(item) for item in payload["nodes"])
        edges = tuple(_edge_from_dict(item) for item in payload["edges"])
        return cls(
            schema_version=GRAPH_SCHEMA_VERSION,
            subject_key=subject_key,
            structure_version=structure_version,
            catalog_revision=catalog_revision,
            as_of_date=as_of_date,
            nodes=nodes,
            edges=edges,
            diagnostics=diagnostics,
        )


def _require_non_empty_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} 必须是非空字符串：{value!r}")
    return value


def _require_index(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} 必须是非负整数：{value!r}")
    return value


def _require_unit_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须是数值：{value!r}")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{label} 必须是 0～1 的有限数值：{value!r}")
    return number


def _require_str_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} 必须是字符串数组")
    return tuple(value)


def _node_from_dict(payload: object) -> ChapterNode:
    if not isinstance(payload, dict):
        raise ValueError("节点必须是 JSON 对象")
    missing = sorted(_NODE_DICT_KEYS - payload.keys())
    extra = sorted(payload.keys() - _NODE_DICT_KEYS)
    if missing:
        raise ValueError("节点缺少字段：" + "、".join(missing))
    if extra:
        raise ValueError("节点包含未知字段：" + "、".join(extra))
    learning_state = payload["learning_state"]
    if learning_state not in LEARNING_STATES:
        raise ValueError(f"非法学习状态：{learning_state!r}")
    mastery_point = payload["mastery_point"]
    if mastery_point is not None:
        mastery_point = _require_unit_float(mastery_point, "mastery_point")
    mastery_interval = payload["mastery_interval"]
    if mastery_interval is not None:
        if not isinstance(mastery_interval, (list, tuple)) or len(mastery_interval) != 2:
            raise ValueError("mastery_interval 必须是 [low, high]")
        low = _require_unit_float(mastery_interval[0], "mastery_interval[0]")
        high = _require_unit_float(mastery_interval[1], "mastery_interval[1]")
        if low > high:
            raise ValueError("mastery_interval 下界不得大于上界")
        mastery_interval = (low, high)
    return ChapterNode(
        module_key=_require_non_empty_str(payload["module_key"], "module_key"),
        display_name=_require_non_empty_str(payload["display_name"], "display_name"),
        module_order=_require_index(payload["module_order"], "module_order"),
        topological_rank=_require_index(payload["topological_rank"], "topological_rank"),
        learning_state=learning_state,
        mastery_point=mastery_point,
        mastery_interval=mastery_interval,
        coverage_count=_require_index(payload["coverage_count"], "coverage_count"),
        topic_count=_require_index(payload["topic_count"], "topic_count"),
        evidence_confidence=_require_unit_float(payload["evidence_confidence"], "evidence_confidence"),
        diagnostics=_require_str_list(payload["diagnostics"], "node.diagnostics"),
    )


def _edge_from_dict(payload: object) -> ChapterEdge:
    if not isinstance(payload, dict):
        raise ValueError("边必须是 JSON 对象")
    missing = sorted(_EDGE_DICT_KEYS - payload.keys())
    extra = sorted(payload.keys() - _EDGE_DICT_KEYS)
    if missing:
        raise ValueError("边缺少字段：" + "、".join(missing))
    if extra:
        raise ValueError("边包含未知字段：" + "、".join(extra))
    relation_type = payload["relation_type"]
    if relation_type not in RELATION_TYPES:
        raise ValueError(f"非法关系类型：{relation_type!r}")
    return ChapterEdge(
        from_module_key=_require_non_empty_str(payload["from_module_key"], "from_module_key"),
        to_module_key=_require_non_empty_str(payload["to_module_key"], "to_module_key"),
        relation_type=relation_type,
        source=_require_non_empty_str(payload["source"], "source"),
        evidence_refs=_require_str_list(payload["evidence_refs"], "evidence_refs"),
        diagnostics=_require_str_list(payload["diagnostics"], "edge.diagnostics"),
    )


@dataclass(frozen=True)
class _TopicEvidence:
    """单个绑定知识点的覆盖/评估聚合（只读计算结果）。"""

    covered: bool
    assessment_values: tuple[float, ...]
    invalid_count: int

    @property
    def has_mastery(self) -> bool:
        return bool(self.assessment_values)

    @property
    def mastery_value(self) -> float | None:
        if not self.assessment_values:
            return None
        return (MASTERY_PRIOR_ALPHA + sum(self.assessment_values)) / (
            MASTERY_PRIOR_STRENGTH + len(self.assessment_values)
        )

    @property
    def confidence(self) -> float:
        count = len(self.assessment_values)
        return count / (count + MASTERY_PRIOR_STRENGTH) if count else 0.0


def build_chapter_graph_snapshot(
    subject_key: object,
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
    as_of_date: str,
    include_archived: bool = False,
) -> ChapterGraphSnapshot:
    """只读构建章节图快照；任何输入变化都会产生全新快照（缓存失效由键保证）。

    ``as_of_date`` 接受 ``YYYY-MM-DD`` 字符串或 ``date``/``datetime`` 对象，
    统一规范化为字符串后走同一校验入口。
    """
    as_of_date = _coerce_as_of_date(as_of_date)
    from study_app.core.chapter_graph_topology import topological_rank, validate_strong_dag

    valid_subject = validate_subject_key(subject_key)
    valid_date = validate_calendar_date(as_of_date, label="as_of_date")
    try:
        catalog = load_catalog_snapshot(db_path)
    except (
        SubjectLifecycleNotInstalledError,
        DatabaseNotInitializedError,
        sqlite3.Error,
        RuntimeError,
    ):
        return _degenerate_snapshot(valid_subject, (STRUCTURE_MISSING,), valid_date)

    subject = catalog.by_key().get(valid_subject)
    if subject is None:
        return _degenerate_snapshot(
            valid_subject, (SUBJECT_UNKNOWN,), valid_date, catalog.catalog_revision
        )

    archived = subject.lifecycle_status == "archived"
    if archived and not include_archived:
        return ChapterGraphSnapshot(
            schema_version=GRAPH_SCHEMA_VERSION,
            subject_key=valid_subject,
            structure_version=subject.structure_version,
            catalog_revision=catalog.catalog_revision,
            as_of_date=valid_date,
            nodes=(),
            edges=(),
            diagnostics=(SUBJECT_ARCHIVED,),
        )

    if subject.structure_version is None:
        return ChapterGraphSnapshot(
            schema_version=GRAPH_SCHEMA_VERSION,
            subject_key=valid_subject,
            structure_version=None,
            catalog_revision=catalog.catalog_revision,
            as_of_date=valid_date,
            nodes=(),
            edges=(),
            diagnostics=(STRUCTURE_VERSION_MISSING,),
        )

    diagnostics: list[str] = []
    modules = sorted(subject.modules, key=lambda item: (item.module_order, item.module_key))
    if not modules:
        return ChapterGraphSnapshot(
            schema_version=GRAPH_SCHEMA_VERSION,
            subject_key=valid_subject,
            structure_version=subject.structure_version,
            catalog_revision=catalog.catalog_revision,
            as_of_date=valid_date,
            nodes=(),
            edges=(),
            diagnostics=(STRUCTURE_EMPTY,),
        )

    table_names, prereq_rows, read_failed = _read_relation_tables(db_path)
    if read_failed:
        diagnostics.append(DB_READ_FAILED)
    if (
        not read_failed
        and (
            "knowledge_topic_registry" not in table_names
            or "knowledge_prerequisites" not in table_names
        )
    ):
        diagnostics.append(REGISTRY_MISSING)

    records: tuple[dict, ...] = ()
    if not read_failed and "learning_records" in table_names:
        try:
            records = tuple(load_raw_records(db_path))
        except (DatabaseNotInitializedError, sqlite3.Error):
            diagnostics.append(DB_READ_FAILED)
            records = ()

    alias_keys = _subject_alias_keys(subject)
    nodes: list[ChapterNode] = []
    topic_to_module: dict[str, str] = {}
    for module in modules:
        evidence = {
            topic.topic_key: _topic_evidence(
                records,
                _safe_normalize(topic.name),
                alias_keys,
                valid_date,
            )
            for topic in module.topics
        }
        for topic in module.topics:
            topic_to_module[topic.topic_key] = module.module_key
        nodes.append(
            _build_node(
                module,
                evidence,
                archived=archived,
            )
        )
    nodes_tuple = tuple(nodes)

    edges, unmapped, self_loops = _project_strong_edges(prereq_rows, topic_to_module)
    if unmapped:
        diagnostics.append(PREREQUISITE_ENDPOINT_UNMAPPED)
    if self_loops:
        diagnostics.append(PREREQUISITE_SELF_LOOP_DROPPED)

    module_keys = tuple(module.module_key for module in modules)
    validation = validate_strong_dag(edges, module_keys)
    cycle_messages = tuple(item for item in validation if item.startswith("循环路径: "))
    if cycle_messages:
        # 历史遗留循环：不伪造拓扑顺序，回退为按原始课程顺序编号（§8.3）。
        for message in cycle_messages:
            diagnostics.append(RELATIONS_CYCLE_PREFIX + message)
        ranked_nodes = tuple(
            replace(node, topological_rank=index)
            for index, node in enumerate(nodes_tuple)
        )
    else:
        ranked_nodes = topological_rank(nodes_tuple, edges)

    order_by_key = {module.module_key: module.module_order for module in modules}
    conflict_keys: set[str] = set()
    for edge in edges:
        if edge.relation_type != "strong_prerequisite":
            continue
        if (
            edge.from_module_key in order_by_key
            and edge.to_module_key in order_by_key
            and order_by_key[edge.from_module_key] > order_by_key[edge.to_module_key]
        ):
            conflict_keys.add(edge.from_module_key)
            conflict_keys.add(edge.to_module_key)
    if conflict_keys:
        ranked_nodes = tuple(
            replace(
                node,
                diagnostics=_dedupe(
                    node.diagnostics + ((NODE_ORDER_CONFLICT,) if node.module_key in conflict_keys else ())
                ),
            )
            for node in ranked_nodes
        )

    if not edges:
        diagnostics.append(RELATIONS_MISSING)

    snapshot_nodes = tuple(
        sorted(ranked_nodes, key=lambda item: (item.module_order, item.module_key))
    )
    return ChapterGraphSnapshot(
        schema_version=GRAPH_SCHEMA_VERSION,
        subject_key=valid_subject,
        structure_version=subject.structure_version,
        catalog_revision=catalog.catalog_revision,
        as_of_date=valid_date,
        nodes=snapshot_nodes,
        edges=edges,
        diagnostics=_dedupe(tuple(diagnostics)),
    )


def _degenerate_snapshot(
    subject_key: str,
    diagnostics: tuple[str, ...],
    as_of_date: str,
    catalog_revision: int | None = None,
) -> ChapterGraphSnapshot:
    return ChapterGraphSnapshot(
        schema_version=GRAPH_SCHEMA_VERSION,
        subject_key=subject_key,
        structure_version=None,
        catalog_revision=catalog_revision,
        as_of_date=as_of_date,
        nodes=(),
        edges=(),
        diagnostics=diagnostics,
    )


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _safe_normalize(value: object) -> str | None:
    try:
        return normalize_alias(value)
    except (ValueError, TypeError):
        return None


def _subject_alias_keys(subject) -> frozenset[str]:
    keys = {
        normalized
        for normalized in (
            _safe_normalize(subject.canonical_name),
            *(_safe_normalize(alias) for alias in subject.aliases),
        )
        if normalized is not None
    }
    return frozenset(keys)


def _read_relation_tables(db_path: Path | str):
    """只读读取先修关系行与表清单；失败返回 read_failed=True（不抛出）。"""
    try:
        with connect_readonly(db_path) as connection:
            names = frozenset(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            )
            rows = ()
            if "knowledge_prerequisites" in names:
                rows = tuple(
                    connection.execute(
                        """
                        SELECT topic_key, prerequisite_topic_key, source
                        FROM knowledge_prerequisites
                        ORDER BY topic_key, prerequisite_topic_key
                        """
                    ).fetchall()
                )
            return names, rows, False
    except (DatabaseNotInitializedError, sqlite3.Error):
        return frozenset(), (), True


def _record_matches_topic(record: dict, topic_norm: str | None) -> tuple[bool, list[dict]]:
    """返回 (是否命中该知识点, 命中的 problem 列表)。"""
    if topic_norm is None:
        return False, []
    direct = _safe_normalize(record.get("topic")) == topic_norm
    problems = record.get("problems") or []
    if not isinstance(problems, list):
        return direct, []
    matched = []
    for problem in problems:
        if not isinstance(problem, dict):
            continue
        related = problem.get("related_topics")
        if not isinstance(related, list):
            continue
        for item in related:
            if _safe_normalize(item) == topic_norm:
                matched.append(problem)
                break
    return direct or bool(matched), matched


def _finite_unit(value: object) -> float | None:
    """有限且落在 0～1 的数值原样返回；其余返回 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return None
    return number


def _topic_evidence(
    records: tuple[dict, ...],
    topic_norm: str | None,
    alias_keys: frozenset[str],
    as_of_date: str,
) -> _TopicEvidence:
    covered = False
    values: list[float] = []
    invalid = 0
    if topic_norm is None:
        return _TopicEvidence(covered=False, assessment_values=(), invalid_count=0)
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            record_date = validate_calendar_date(record.get("date"), label="record.date")
        except ValueError:
            continue
        if record_date > as_of_date:
            continue
        subject_norm = _safe_normalize(record.get("subject"))
        if subject_norm is None or subject_norm not in alias_keys:
            continue
        matched, matched_problems = _record_matches_topic(record, topic_norm)
        if not matched:
            continue
        covered = True
        score = record.get("score")
        if score is not None:
            unit_score = None
            if not isinstance(score, bool) and isinstance(score, (int, float)):
                number = float(score)
                if math.isfinite(number) and 0.0 <= number <= 100.0:
                    unit_score = number / 100.0
            if unit_score is None:
                invalid += 1
            else:
                values.append(unit_score)
        for problem in matched_problems:
            candidate = problem.get("partial_credit")
            if candidate is None:
                candidate = problem.get("correctness")
            unit = _finite_unit(candidate)
            if unit is None:
                if candidate is not None:
                    invalid += 1
                continue
            values.append(unit)
    return _TopicEvidence(
        covered=covered,
        assessment_values=tuple(values),
        invalid_count=invalid,
    )


def _build_node(
    module: CatalogModule,
    evidence: dict,
    *,
    archived: bool,
) -> ChapterNode:
    topics = module.topics
    topic_count = len(topics)
    diagnostics: list[str] = []
    if topic_count == 0:
        diagnostics.append(NODE_STRUCTURE_TOPICS_UNBOUND)
    coverage_count = sum(1 for topic in topics if evidence[topic.topic_key].covered)
    contributing = [
        (topic, evidence[topic.topic_key])
        for topic in topics
        if evidence[topic.topic_key].has_mastery
    ]
    excluded_without_mastery = sum(
        1
        for topic in topics
        if evidence[topic.topic_key].covered and not evidence[topic.topic_key].has_mastery
    )
    if excluded_without_mastery:
        diagnostics.append(f"{NODE_MASTERY_TOPICS_EXCLUDED_PREFIX}{excluded_without_mastery}")
    invalid_total = sum(evidence[topic.topic_key].invalid_count for topic in topics)
    if invalid_total:
        diagnostics.append(f"{NODE_MASTERY_INVALID_EVIDENCE_PREFIX}{invalid_total}")

    if topic_count == 0 or coverage_count == 0:
        computed_state = "unlearned"
    elif coverage_count < topic_count:
        computed_state = "partial"
    elif contributing:
        computed_state = "learned_assessed"
    else:
        computed_state = "learned_unassessed"

    mastery_point: float | None = None
    mastery_interval_value: tuple[float, float] | None = None
    evidence_confidence = 0.0
    if contributing:
        point = _weighted_mastery(
            [
                (topic.importance_bp, evidence[topic.topic_key].mastery_value)
                for topic, _ in contributing
            ]
        )
        if point is None or not math.isfinite(point):
            # 防御分支：聚合结果非有限时按无效证据排除，绝不输出非有限数值。
            invalid_total += 1
            diagnostics.append(f"{NODE_MASTERY_INVALID_EVIDENCE_PREFIX}{invalid_total}")
        else:
            mastery_point = point
            total_observations = sum(
                len(evidence[topic.topic_key].assessment_values) for topic, _ in contributing
            )
            evidence_confidence = sum(
                evidence[topic.topic_key].confidence for topic, _ in contributing
            ) / len(contributing)
            evidence_confidence *= len(contributing) / max(topic_count, 1)
            if not math.isfinite(evidence_confidence):
                evidence_confidence = 0.0
                invalid_total += 1
                diagnostics.append(f"{NODE_MASTERY_INVALID_EVIDENCE_PREFIX}{invalid_total}")
            else:
                try:
                    interval = existing_mastery_interval(
                        mastery_point, evidence_confidence, total_observations
                    )
                except (ValueError, TypeError):
                    interval = None
                if interval is not None and all(math.isfinite(v) for v in interval):
                    mastery_interval_value = interval

    learning_state = computed_state
    if archived:
        learning_state = "archived"
        diagnostics.append(NODE_ARCHIVED_READONLY)
        diagnostics.append(f"{NODE_ARCHIVED_COMPUTED_STATE_PREFIX}{computed_state}")

    return ChapterNode(
        module_key=module.module_key,
        display_name=module.display_name,
        module_order=module.module_order,
        topological_rank=0,
        learning_state=learning_state,
        mastery_point=mastery_point,
        mastery_interval=mastery_interval_value,
        coverage_count=coverage_count,
        topic_count=topic_count,
        evidence_confidence=evidence_confidence,
        diagnostics=_dedupe(tuple(diagnostics)),
    )


def _weighted_mastery(weighted_values: list[tuple[object, float | None]]) -> float | None:
    """importance_bp 归一化加权；缺失权重用已有均值补齐；全缺/全零回退等权。"""
    usable = [(bp, value) for bp, value in weighted_values if value is not None]
    if not usable:
        return None
    bps = [bp for bp, _ in usable]
    if all(bp is None for bp in bps):
        weights = [1.0 for _ in usable]
    elif all(bp is not None for bp in bps):
        weights = [float(bp) for bp in bps]
    else:
        known = [float(bp) for bp in bps if bp is not None]
        fallback = sum(known) / len(known)
        weights = [float(bp) if bp is not None else fallback for bp in bps]
    total = sum(weights)
    if not math.isfinite(total) or total <= 0.0:
        weights = [1.0 for _ in usable]
        total = float(len(usable))
    point = sum(weight * value for weight, (_, value) in zip(weights, usable)) / total
    return point if math.isfinite(point) else None


def _project_strong_edges(prereq_rows, topic_to_module: dict[str, str]):
    """把知识点级先修行聚合为模块级强边；无法映射的丢弃并给出诊断。"""
    unmapped = False
    self_loops = 0
    merged: dict[tuple[str, str], tuple[set[str], set[str]]] = {}
    for row in prereq_rows:
        topic_key = str(row["topic_key"])
        prerequisite_key = str(row["prerequisite_topic_key"])
        from_key = topic_to_module.get(prerequisite_key)
        to_key = topic_to_module.get(topic_key)
        if from_key is None or to_key is None:
            unmapped = True
            continue
        if from_key == to_key:
            self_loops += 1
            continue
        sources, refs = merged.setdefault((from_key, to_key), (set(), set()))
        sources.add(str(row["source"]))
        refs.add(f"{prerequisite_key}->{topic_key}")
    edges = []
    for (from_key, to_key), (sources, refs) in sorted(merged.items()):
        ordered_sources = sorted(sources)
        source = ordered_sources[0] if len(ordered_sources) == 1 else "mixed"
        edges.append(
            ChapterEdge(
                from_module_key=from_key,
                to_module_key=to_key,
                relation_type="strong_prerequisite",
                source=source,
                evidence_refs=tuple(sorted(refs)),
                diagnostics=(),
            )
        )
    return tuple(edges), unmapped, self_loops


def list_graph_subjects(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    include_archived: bool = False,
) -> tuple[dict, ...]:
    """图可选学科清单：active 在前（名称序），归档学科仅在显式开启时出现。"""
    try:
        catalog: CatalogSnapshot | None = load_catalog_snapshot(db_path)
    except (
        SubjectLifecycleNotInstalledError,
        DatabaseNotInitializedError,
        sqlite3.Error,
        RuntimeError,
    ):
        return ()
    assert catalog is not None
    rows = []
    for subject in catalog.subjects:
        if subject.lifecycle_status != "active" and not include_archived:
            continue
        rows.append(
            {
                "subject_key": subject.subject_key,
                "display_name": subject.display_name,
                "lifecycle_status": subject.lifecycle_status,
                "has_current_structure": bool(subject.structure_version)
                and len(subject.modules) > 0,
            }
        )
    rows.sort(
        key=lambda item: (
            0 if item["lifecycle_status"] == "active" else 1,
            unicodedata.normalize("NFKC", item["display_name"]),
            item["subject_key"],
        )
    )
    return tuple(rows)


def _records_digest_from_connection(connection) -> str:
    records = load_raw_records_from_connection(connection)
    minimal = []
    for record in records:
        if not isinstance(record, dict) or record.get("id") is None:
            continue
        minimal.append(
            {
                field: record.get(field)
                for field in ("id", "date", "subject", "module", "topic", "activity", "source", "score")
            }
        )
    minimal.sort(key=lambda item: str(item["id"]))
    payload = json.dumps(
        minimal, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _coerce_as_of_date(value: object) -> str:
    """接受 ``YYYY-MM-DD`` 字符串或 ``date``/``datetime``，统一为字符串。"""
    from datetime import date as _date
    from datetime import datetime as _datetime

    if isinstance(value, _datetime):
        return value.date().isoformat()
    if isinstance(value, _date):
        return value.isoformat()
    if isinstance(value, str):
        return value
    raise TypeError(f"as_of_date 必须是 YYYY-MM-DD 字符串或 date：{value!r}")


def snapshot_cache_key(
    subject_key: object,
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
    as_of_date: object,
) -> tuple:
    """快照缓存键：(subject_key, structure_version, catalog_revision, as_of_date,
    records_digest, prereq_version)。任何读取失败都返回包含
    ``structure_missing`` 诊断的退化键，绝不抛出、绝不写库。"""
    as_of_date = _coerce_as_of_date(as_of_date)
    key_subject = str(subject_key)
    valid_date = validate_calendar_date(as_of_date, label="as_of_date")
    """快照缓存键：(subject_key, structure_version, catalog_revision, as_of_date,
    records_digest, prereq_version)。任何读取失败都返回包含
    ``structure_missing`` 诊断的退化键，绝不抛出、绝不写库。"""
    key_subject = str(subject_key)
    valid_date = validate_calendar_date(as_of_date, label="as_of_date")
    try:
        with connect_readonly(db_path) as connection:
            revision_row = connection.execute(
                "SELECT catalog_revision FROM subject_catalog_state WHERE singleton = 1"
            ).fetchone()
            revision = None if revision_row is None else int(revision_row["catalog_revision"])
            version_row = connection.execute(
                """
                SELECT structure_version FROM subject_structure_versions
                WHERE subject_key = ? AND is_current = 1
                ORDER BY structure_version LIMIT 1
                """,
                (key_subject,),
            ).fetchone()
            version = None if version_row is None else str(version_row["structure_version"])
            digest = _records_digest_from_connection(connection)
            prereq_version = None
            prereq_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_prerequisites'"
            ).fetchone()
            if prereq_table is not None:
                count_row = connection.execute(
                    "SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS m FROM knowledge_prerequisites"
                ).fetchone()
                prereq_version = (int(count_row["n"]), int(count_row["m"]))
    except (DatabaseNotInitializedError, sqlite3.Error):
        return (STRUCTURE_MISSING, key_subject, valid_date)
    return (key_subject, version, revision, valid_date, digest, prereq_version)


class ChapterGraphCache:
    """进程内快照缓存：键为 :func:`snapshot_cache_key` 的元组。

    仅用于 UI 层复用；构建器本身永远重算，绝不返回过期快照。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple, ChapterGraphSnapshot] = {}

    def get(self, key) -> ChapterGraphSnapshot | None:
        with self._lock:
            return self._entries.get(tuple(key))

    def put(self, key, snapshot) -> None:
        if not isinstance(snapshot, ChapterGraphSnapshot):
            raise TypeError("缓存只能存放 ChapterGraphSnapshot")
        with self._lock:
            self._entries[tuple(key)] = snapshot
