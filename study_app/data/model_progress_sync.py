from __future__ import annotations

import hashlib
import json
import copy
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from learning_bkt import clamp, normalize_topic_text, topic_matches_text
from learning_problem_result import interpret_problem_result
from study_app.core.mastery_trace import (
    SYNC_ALGORITHM_VERSION, TRACE_VERSION, append_mastery_trace,
    freeze_replay_baseline,
    record_fingerprint,
)

from study_app.data.database import (
    DEFAULT_DB_PATH,
    connect,
    dumps,
    ensure_seeded_database,
    get_setting,
    require_initialized_database,
    set_setting,
)
from study_app.paths import MODEL_PATH


LEARNED_STATUS = "learned_needs_review"
_MODEL_SYNC_LOCKS: dict[str, threading.RLock] = {}
_MODEL_SYNC_LOCKS_GUARD = threading.Lock()
_MODEL_SYNC_REENTRANT = threading.local()


def _model_path_identity(path: Path) -> str:
    resolved = str(path.resolve())
    return os.path.normcase(resolved) if os.name == "nt" else resolved


@contextmanager
def _exclusive_model_sync(path: Path):
    identity = _model_path_identity(path)
    with _MODEL_SYNC_LOCKS_GUARD:
        process_lock = _MODEL_SYNC_LOCKS.setdefault(identity, threading.RLock())
    with process_lock:
        if identity in getattr(_MODEL_SYNC_REENTRANT, "identities", set()):
            yield
            return
    lock_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    lock_dir = Path(tempfile.gettempdir()) / "study-app-model-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{lock_digest}.lock"

    with process_lock, lock_path.open("a+b") as lock_file:
        identities = getattr(_MODEL_SYNC_REENTRANT, "identities", None)
        if identities is None:
            identities = set()
            _MODEL_SYNC_REENTRANT.identities = identities
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            identities.add(identity)
            try:
                yield
            finally:
                identities.remove(identity)
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            identities.add(identity)
            try:
                yield
            finally:
                identities.remove(identity)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _pending_model_sync_key(path: Path) -> str:
    identity = _model_path_identity(path).encode("utf-8")
    return f"pending_model_sync:{hashlib.sha256(identity).hexdigest()}"


def _recover_pending_model_sync(path: Path, db_path: Path | str) -> None:
    key = _pending_model_sync_key(path)
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT value_json FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
    if not row:
        return
    payload = json.loads(row["value_json"])
    model_json = payload.get("model_json") if isinstance(payload, dict) else None
    payload_path = payload.get("model_path") if isinstance(payload, dict) else None
    if not isinstance(payload_path, str) or _model_path_identity(Path(payload_path)) != _model_path_identity(path):
        raise ValueError(f"pending model sync journal path mismatch: {key}")
    if not isinstance(model_json, str):
        raise ValueError(f"invalid pending model sync journal: {key}")

    recovery_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        recovery_path.write_text(model_json, encoding="utf-8")
        recovery_path.replace(path)
        with connect(db_path) as connection:
            connection.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    finally:
        recovery_path.unlink(missing_ok=True)

TOPIC_ALIASES = {
    "积分公式综合题": ("积分与场论公式综合题", "积分公式综合", "三大积分公式综合"),
    "一致收敛性与逐项运算": ("一致收敛", "函数项级数", "逐项求导", "逐项积分", "逐项运算"),
    "归并与基数排序": ("归并排序", "基数排序", "二路归并"),
    "外部排序": ("外排序", "多路归并"),
    "AVL 树旋转与插入删除": ("AVL", "AVL树", "AVL 插入", "AVL 删除", "平衡因子", "旋转类型"),
    "图的存储": ("邻接矩阵", "邻接表", "图的表示", "图的存储"),
    "DFS 与 BFS": ("DFS", "BFS", "深度优先", "广度优先"),
    "内部排序": (
        "内排序",
        "内部排序",
        "直接插入排序",
        "折半插入排序",
        "希尔排序",
        "快速排序",
        "直接选择排序",
        "堆排序",
        "归并排序",
    ),
    "外部排序": ("外排序", "外部排序"),
    "堆与优先队列": ("堆排序", "堆", "优先队列"),
    "常数项级数与判敛法": (
        "常数项级数",
        "级数判别法",
        "正项级数",
        "交错级数",
        "绝对收敛",
        "条件收敛",
        "Leibniz判别法",
    ),
    "正项级数与比较判别法": ("正项级数", "比较判别法", "积分判别法", "根值判别法", "比值判别法"),
    "幂级数与收敛半径": ("幂级数", "收敛半径", "收敛域"),
    "函数展开为幂级数": ("Taylor级数", "泰勒级数", "函数展开为幂级数"),
    "梯度、散度、旋度的综合理解": ("梯度", "散度", "旋度", "场论基础"),
    "无源场与无旋场": ("无源场", "无旋场", "势函数"),
    "零级、一级、二级、三级反应积分式": ("零级反应", "一级反应", "二级反应", "三级反应", "积分速率方程"),
    "半衰期与初始浓度关系": ("半衰期", "初始浓度"),
    "哈希表": ("哈希表", "散列表", "散列", "二次探测"),
    "数据结构基本概念与抽象数据类型": ("数据结构", "抽象数据类型", "ADT", "逻辑结构", "存储结构"),
    "算法复杂度分析": ("复杂度", "时间复杂度", "空间复杂度", "Big-O", "主定理"),
    "顺序表": ("线性表", "顺序表", "顺序存储"),
    "单链表、循环链表与双向链表": ("链表", "单链表", "循环链表", "双向链表"),
    "栈及其应用": ("栈", "括号匹配", "表达式"),
    "队列及其应用": ("队列", "循环队列"),
    "递归与非递归转换": ("递归", "非递归"),
    "多维数组、特殊矩阵与稀疏矩阵": ("数组", "特殊矩阵", "稀疏矩阵", "压缩存储"),
    "串与 KMP 算法": ("串", "字符串", "KMP", "next数组"),
    "广义表": ("广义表",),
    "树、森林与二叉树": ("树", "森林", "二叉树", "线索二叉树", "二叉树遍历"),
    "哈夫曼树与编码": ("哈夫曼", "Huffman", "带权路径长度"),
    "等价类与并查集": ("等价类", "并查集", "路径压缩"),
    "字典与哈希表": ("字典", "哈希表", "散列表", "二次探测", "装填因子", "平均查找长度"),
    "静态搜索结构": ("静态搜索", "顺序查找", "折半查找", "二分查找"),
    "图的存储与遍历": ("图的存储", "邻接矩阵", "邻接表", "DFS", "BFS"),
    "最小生成树与最短路径": ("最小生成树", "Prim", "Kruskal", "最短路径", "Dijkstra", "Floyd"),
    "拓扑排序与关键路径": ("拓扑排序", "关键路径", "AOE"),
    "插入排序与希尔排序": ("插入排序", "希尔排序"),
    "交换排序与快速排序": ("交换排序", "冒泡排序", "快速排序", "快排"),
    "选择排序与堆排序": ("选择排序", "堆排序"),
    "归并、基数与外部排序": ("归并排序", "基数排序", "外部排序", "外排序"),
    "Lorentz 变换": ("Lorentz", "洛伦兹变换", "洛伦兹", "同时性的相对性", "同时性"),
    "时间膨胀与长度收缩": ("时间膨胀", "长度收缩", "钟慢", "尺缩"),
    "相对论动力学基础": (
        "相对论动力学",
        "相对论速度变换",
        "相对论多普勒",
        "多普勒效应",
        "质能关系",
        "能量-动量关系",
        "超相对论近似",
        "粒子衰变",
        "衰变运动学",
        "二维动量守恒",
    ),
}

MODULE_ALIASES = {
    "第9章：狭义相对论": (
        "第9章",
        "第九章",
        "狭义相对论",
        "相对论专项",
        "相对论综合训练",
    ),
}

TOPIC_ALIASES.update(
    {
        "相对性原理与光速不变": (
            "相对性原理",
            "光速不变",
            "惯性系",
            "时空间隔",
            "类时",
            "类空",
            "类光",
            "双事件",
            "同时性",
        ),
        "Lorentz 变换": (
            "Lorentz",
            "洛伦兹变换",
            "Lorentz变换",
            "洛伦兹",
            "坐标变换",
            "同时性",
            "四动量Lorentz变换",
        ),
        "时间膨胀与长度收缩": (
            "时间膨胀",
            "长度收缩",
            "固有时间",
            "固有长度",
            "寿命",
            "斜放杆",
            "非平行长度收缩",
        ),
        "相对论动力学基础": (
            "相对论动力学",
            "速度变换",
            "相对论速度变换",
            "Doppler",
            "多普勒",
            "红移",
            "蓝移",
            "总能量",
            "动能",
            "相对论动量",
            "能量-动量关系",
            "四动量",
            "四动量不变量",
            "质心系",
            "质心系速度",
            "二体衰变",
            "实验室系变换",
            "能量-动量守恒",
        ),
    }
)

STATUS_RANK = {
    "not_started": 0,
    "learning": 1,
    "in_progress": 1,
    "current": 1,
    "learned": 2,
    "learned_needs_review": 2,
    "reviewing": 3,
    "mastered": 4,
}


def is_learning_evidence_record(record: dict[str, Any]) -> bool:
    activity = str(record.get("activity") or "")
    source = str(record.get("source") or "")
    return (
        source in {"classroom", "class", "outside_class", "self_study", "ai_generated_pdf", "planned_homework"}
        or activity
        in {
            "class",
            "class_learning",
            "class_exercise",
            "practice",
            "review",
            "exercise",
            "review_exercise",
            "self_test",
            "plan_completion",
        }
    )


def sync_learning_record_to_model(
    record: dict[str, Any],
    model_path: Path | str = MODEL_PATH,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[str]:
    if not is_learning_evidence_record(record):
        return []
    if not str(record.get("subject") or ""):
        return []
    path = Path(model_path)
    with _exclusive_model_sync(path):
        return _sync_learning_record_to_model_unlocked(record, path, db_path)


def _sync_learning_record_to_model_unlocked(
    record: dict[str, Any],
    model_path: Path | str = MODEL_PATH,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[str]:
    if not is_learning_evidence_record(record):
        return []

    subject_name = str(record.get("subject") or "")
    if not subject_name:
        return []
    path = Path(model_path)
    require_initialized_database(db_path)
    from study_app.data.database import get_f5_subject_lifecycle_status

    if get_f5_subject_lifecycle_status(subject_name, db_path) == "archived":
        return []
    _recover_pending_model_sync(path, db_path)
    from study_app.core.study_phase import ARCHIVED_PHASE, phase_setting_key

    phase = get_setting(phase_setting_key(subject_name), {}, db_path)
    if isinstance(phase, dict) and phase.get("phase") == ARCHIVED_PHASE:
        return []

    original_model_bytes = path.read_bytes()
    model = json.loads(original_model_bytes.decode("utf-8"))
    model_snapshot_before = hashlib.sha256(original_model_bytes).hexdigest()
    record_text = record_learning_text(record)
    changed: list[tuple[str, str]] = []
    contribution_items: list[dict[str, Any]] = []
    contribution_token = mastery_contribution_token(record)
    contribution_already_applied = bool(
        contribution_token
        and get_setting(
            f"mastery_contribution_applied:{contribution_token}",
            False,
            db_path,
        )
    )

    for subject in model.get("subjects", []):
        if subject.get("name") != subject_name:
            continue
        diagnostic_baseline = (
            not contribution_already_applied
            and is_diagnostic_baseline_record(record)
        )
        for module in subject.get("modules", []):
            module_changed = False
            module_before_contribution = copy.deepcopy(module)
            module_explicit = module_has_explicit_evidence(module, record)
            for topic in module.get("topics", []):
                topic_before_record = copy.deepcopy(topic)
                topic_in_scope = diagnostic_baseline_topic_in_scope(
                    subject_name,
                    str(module.get("name") or ""),
                    str(topic.get("name") or ""),
                    str(topic.get("submodule") or ""),
                    phase=phase,
                )
                if (
                    not topic_in_scope
                    and not module_explicit
                    and not topic_has_learning_evidence(topic, record_text)
                ):
                    continue
                topic_changed = False
                if STATUS_RANK.get(str(topic.get("status") or ""), 0) >= STATUS_RANK[LEARNED_STATUS]:
                    pass
                else:
                    topic["status"] = LEARNED_STATUS
                    topic_changed = True
                if not contribution_already_applied:
                    if topic_in_scope:
                        contribution = apply_diagnostic_baseline_contribution(record, module, topic)
                    else:
                        contribution = apply_mastery_contribution(record, module, topic)
                    if contribution:
                        freeze_replay_baseline(
                            topic, topic_before_record,
                            record_id=record.get("id"), db_path=db_path,
                        )
                        evidence_items = contribution.pop("_trace_evidence_items", [])
                        append_mastery_trace(
                            topic,
                            {
                                "trace_version": TRACE_VERSION,
                                "sync_algorithm_version": SYNC_ALGORITHM_VERSION,
                                "model_version": SYNC_ALGORITHM_VERSION,
                                "model_identity": model.get("model_name"),
                                "model_snapshot_sha256_before": model_snapshot_before,
                                "normalization_version": record.get("_normalization_version"),
                                "record_id": record.get("id"),
                                "record_token": contribution_token,
                                "record_date": record.get("date"),
                                "record_fingerprint": record_fingerprint(record),
                                "subject": subject_name,
                                "module": module.get("name"),
                                "topic": topic.get("name"),
                                "mode": contribution.get("mode", "mastery_update"),
                                "old_mastery": contribution.get("old_mastery"),
                                "new_mastery": contribution.get("new_mastery"),
                                "delta": contribution.get("delta"),
                                "result_correctness": contribution.get("correctness"),
                                "difficulty_score": contribution.get("difficulty_score"),
                                "evidence_items": evidence_items,
                            },
                        )
                        contribution_items.append(contribution)
                        topic_changed = True
                if topic_changed:
                    module_changed = True
                    changed.append((str(module.get("name") or ""), str(topic.get("name") or "")))
            if module_changed and "mastery_replay_baseline_v1" not in module:
                module["mastery_replay_baseline_v1"] = {
                    "status": module_before_contribution.get("status"),
                    "prior_provenance": "unverified",
                }
            if module_changed and STATUS_RANK.get(str(module.get("status") or ""), 0) < STATUS_RANK[LEARNED_STATUS]:
                module["status"] = LEARNED_STATUS
            if module_changed:
                recompute_module_mastery(module)
        recompute_subject_mastery(subject)
        break

    if not changed:
        return []

    model_json = json.dumps(model, ensure_ascii=False, indent=2) + "\n"
    pending_key = _pending_model_sync_key(path)
    tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(model_json, encoding="utf-8")
        with connect(db_path) as connection:
            sync_topic_statuses_to_sqlite(
                subject_name,
                changed,
                model,
                db_path,
                connection=connection,
            )
            if contribution_items:
                set_setting(
                    "last_mastery_contribution_audit",
                    {
                        "record_id": record.get("id"),
                        "date": record.get("date"),
                        "subject": subject_name,
                        "note": record.get("note"),
                        "items": contribution_items,
                    },
                    db_path,
                    connection=connection,
                )
            if contribution_token and not contribution_already_applied:
                set_setting(
                    f"mastery_contribution_applied:{contribution_token}",
                    True,
                    db_path,
                    connection=connection,
                )
            set_setting(
                pending_key,
                {"model_path": str(path.resolve()), "model_json": model_json},
                db_path,
                connection=connection,
            )
        tmp_path.replace(path)
        with connect(db_path) as connection:
            connection.execute("DELETE FROM app_settings WHERE key = ?", (pending_key,))
    finally:
        tmp_path.unlink(missing_ok=True)
    return [topic for _module, topic in changed]


def sync_classroom_record_to_model(
    record: dict[str, Any],
    model_path: Path | str = MODEL_PATH,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[str]:
    """Compatibility wrapper retained for existing callers."""
    return sync_learning_record_to_model(record, model_path=model_path, db_path=db_path)


def record_learning_text(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("module", "topic", "chapter", "note"):
        parts.append(str(record.get(key) or ""))
    related = record.get("related_topics") or []
    if isinstance(related, list):
        parts.extend(str(item) for item in related)
    for problem in record.get("problems", []) or []:
        parts.extend(
            str(problem.get(key) or "")
            for key in ("title", "statement", "related_topics")
        )
    return " ".join(parts)


def topic_has_learning_evidence(topic: dict[str, Any], record_text: str) -> bool:
    name = str(topic.get("name") or "")
    if topic_matches_text(name, record_text):
        return True
    normalized_record = normalize_topic_text(record_text)
    return any(
        normalize_topic_text(alias) in normalized_record
        for alias in TOPIC_ALIASES.get(name, ())
    )


def topic_has_classroom_evidence(topic: dict[str, Any], record_text: str) -> bool:
    """Compatibility wrapper retained for audit scripts."""
    return topic_has_learning_evidence(topic, record_text)


def module_has_explicit_evidence(module: dict[str, Any], record: dict[str, Any]) -> bool:
    module_name = str(module.get("name") or "")
    direct_text = " ".join(
        str(record.get(key) or "")
        for key in ("module", "topic", "chapter", "note")
    )
    normalized_direct = normalize_topic_text(direct_text)
    chapter_prefix = module_name.split("：", 1)[0].strip()
    chapter_match = (
        (chapter_prefix.startswith("第") or chapter_prefix.lower().startswith("chapter"))
        and normalize_topic_text(chapter_prefix) in normalized_direct
        and any(
            marker in record_learning_text(record)
            for marker in (
                "学完",
                "学习",
                "复习",
                "完成",
                "练习",
                "做了",
            )
        )
    )
    alias_match = any(
        normalize_topic_text(alias) in normalized_direct
        for alias in MODULE_ALIASES.get(module_name, ())
    )
    return chapter_match or alias_match


def mastery_contribution_token(record: dict[str, Any]) -> str:
    record_id = record.get("id")
    if record_id is not None:
        return f"id:{record_id}"
    parts = [
        str(record.get(key) or "")
        for key in ("date", "subject", "module", "topic", "activity", "source", "score", "note")
    ]
    return "fingerprint:" + "|".join(parts)[:220]


def record_source_quality(record: dict[str, Any]) -> float:
    source = str(record.get("source") or "")
    note = str(record.get("note") or "")
    text = f"{source} {note}".lower()
    if "ai" in text or "gpt" in text or "generated" in text:
        return 1.0
    if source in {"classroom", "class", "uploaded_homework", "outside_class", "self_study"}:
        return 1.0
    return 0.92


def is_diagnostic_baseline_record(record: dict[str, Any]) -> bool:
    """Treat the first broad diagnostic paper as an exam-scope mastery baseline.

    A diagnostic paper with no parsed problem list still carries useful evidence:
    it tells us the learner's initial exam-range command is not merely the old
    low prior caused by a long gap in records. Detailed problem attempts, when
    available, continue to use normal topic-level contribution instead.
    """
    if record.get("problems"):
        return False
    score = record.get("score")
    if not isinstance(score, (int, float)):
        return False
    activity = str(record.get("activity") or "")
    source = str(record.get("source") or "")
    text = " ".join(
        str(record.get(key) or "")
        for key in ("module", "topic", "note")
    )
    if activity not in {"exam_review", "self_test", "quiz", "review"}:
        return False
    if source not in {"ai_generated_pdf", "uploaded_homework", "outside_class", "self_study"}:
        return False
    return any(marker in text for marker in ("诊断卷", "综合诊断", "诊断模拟卷"))


def diagnostic_baseline_topic_in_scope(
    subject: str,
    module_name: str,
    topic_name: str,
    submodule_name: str = "",
    *,
    phase: dict[str, Any] | None = None,
) -> bool:
    from study_app.core.study_phase import FINAL_REVIEW_PHASE, is_in_exam_scope

    return bool(phase and phase.get("phase") == FINAL_REVIEW_PHASE) and is_in_exam_scope(
        subject,
        module_name,
        topic_name,
        submodule_name,
        phase=phase,
        infer_submodule=False,
    )


def diagnostic_baseline_target(record: dict[str, Any], topic: dict[str, Any]) -> float | None:
    score = record.get("score")
    if not isinstance(score, (int, float)):
        return None
    correctness = clamp(float(score) / 100)
    difficulty = clamp(float(topic.get("difficulty", 0.60) or 0.60))
    # A broad diagnostic paper is a baseline calibration, not proof of mastery.
    # It should correct stale low priors caused by missing records, while still
    # leaving room for later higher-difficulty standard/sprint papers to lift the
    # estimate. Diagnostic difficulty is often intentionally moderate, so keep
    # the ceiling lower than normal topic-level evidence.
    return clamp(0.28 + 0.34 * correctness + 0.10 * difficulty, 0.18, 0.72)


def apply_diagnostic_baseline_contribution(
    record: dict[str, Any],
    module: dict[str, Any],
    topic: dict[str, Any],
) -> dict[str, Any] | None:
    target = diagnostic_baseline_target(record, topic)
    if target is None:
        return None
    old_mastery = clamp(topic.get("mastery", 0.0))
    if target <= old_mastery + 0.002:
        return None
    topic["mastery"] = round(target, 4)
    topic["forgetting_risk"] = round(
        max(0.0, min(1.0, float(topic.get("forgetting_risk", 0.0) or 0.0) * 0.35)),
        4,
    )
    source = topic.get("source_json")
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except json.JSONDecodeError:
            source = {}
    if not isinstance(source, dict):
        source = {}
    source["diagnostic_baseline_update"] = {
        "record_id": record.get("id"),
        "date": record.get("date"),
        "score": record.get("score"),
        "old_mastery": round(old_mastery, 4),
        "new_mastery": round(target, 4),
        "reason": "first_exam_scope_diagnostic_baseline",
    }
    topic["source_json"] = source
    return {
        "module": module.get("name"),
        "topic": topic.get("name"),
        "correctness": round(float(record.get("score", 0)) / 100, 4),
        "difficulty_score": round(float(topic.get("difficulty", 0.60) or 0.60) * 100, 1),
        "expected_correctness": None,
        "delta": round((target - old_mastery) * 100, 1),
        "old_mastery": round(old_mastery * 100, 1),
        "new_mastery": round(target * 100, 1),
        "evidence_quality": record_source_quality(record),
        "mode": "diagnostic_baseline",
        "_trace_evidence_items": [{
            "problem_index": None,
            "problem_id": None,
            "problem_title": None,
            "mapping_source": "diagnostic_exam_scope",
            "mapped_topic": topic.get("name"),
            "result_source": "record_score",
            "result_value": float(record.get("score", 0)) / 100,
            "difficulty_raw": topic.get("difficulty"),
            "difficulty_basis": "topic_difficulty",
            "difficulty_source": "topic_model_fallback",
            "difficulty_applied": float(topic.get("difficulty", 0.60) or 0.60) * 100,
        }],
    }


def problem_text(problem: dict[str, Any]) -> str:
    return " ".join(
        str(problem.get(key) or "")
        for key in ("title", "statement", "note", "error_cause", "related_topics")
    )


def topic_observation_from_record(record: dict[str, Any], topic: dict[str, Any]) -> dict[str, float] | None:
    problems = list(record.get("problems") or [])
    topic_name = str(topic.get("name") or "")
    primary_text = " ".join(
        str(record.get(key) or "")
        for key in ("module", "topic", "related_topics", "note")
    )
    primary_match = topic_has_learning_evidence(topic, primary_text)
    has_problem_topic_metadata = any(problem.get("related_topics") for problem in problems)

    observations: list[tuple[float, float]] = []
    result_sources: list[str] = []
    evidence_items: list[dict[str, Any]] = []
    for problem_index, problem in enumerate(problems):
        related_topics = problem.get("related_topics") or []
        if related_topics:
            matched = topic_has_learning_evidence(topic, " ".join(str(item) for item in related_topics))
            if not matched:
                continue
            mapping_source = "problem_related_topics"
        else:
            matched = topic_has_learning_evidence(topic, problem_text(problem))
            mapping_source = "problem_text" if matched else "record_context"
        if not matched and not primary_match:
            continue
        interpretation = interpret_problem_result(problem, record)
        correctness = interpretation["value"]
        if correctness is None:
            continue
        result_sources.append(interpretation["source"])
        difficulty = problem.get("difficulty_score")
        try:
            difficulty_score = float(difficulty)
            difficulty_source = problem.get("difficulty_source") or None
            difficulty_basis = "problem_difficulty_score"
        except (TypeError, ValueError):
            difficulty_score = float(topic.get("difficulty", 0.60) or 0.60) * 100
            difficulty_source = "topic_model_fallback"
            difficulty_basis = "topic_difficulty"
        applied_difficulty = max(20.0, min(95.0, difficulty_score))
        observations.append((clamp(correctness), applied_difficulty))
        evidence_items.append({
            "problem_index": problem_index,
            "problem_id": problem.get("id"),
            "problem_title": problem.get("title"),
            "mapping_source": mapping_source,
            "mapped_topic": topic_name,
            "result_source": interpretation["source"],
            "result_value": clamp(correctness),
            "difficulty_raw": difficulty,
            "difficulty_basis": difficulty_basis,
            "difficulty_source": difficulty_source,
            "difficulty_applied": applied_difficulty,
        })

    if observations:
        weights = [0.75 + difficulty / 100 for _correctness, difficulty in observations]
        total_weight = sum(weights) or 1.0
        correctness = sum(item[0] * weight for item, weight in zip(observations, weights)) / total_weight
        difficulty = sum(item[1] * weight for item, weight in zip(observations, weights)) / total_weight
        return {
            "correctness": float(correctness),
            "difficulty_score": float(difficulty),
            "observation_count": float(len(observations)),
            "result_sources": list(result_sources),
            "evidence_items": evidence_items,
        }

    if primary_match:
        correctness = interpret_problem_result(None, record)["value"]
        if correctness is None:
            return None
        return {
            "correctness": float(correctness),
            "difficulty_score": float(topic.get("difficulty", 0.60) or 0.60) * 100,
            "observation_count": 1.0,
            "result_sources": ["record_fallback"],
            "evidence_items": [{
                "problem_index": None, "problem_id": None,
                "problem_title": None,
                "mapping_source": "record_context",
                "mapped_topic": topic_name,
                "result_source": "record_fallback",
                "result_value": float(correctness),
                "difficulty_raw": topic.get("difficulty"),
                "difficulty_basis": "topic_difficulty",
                "difficulty_source": "topic_model_fallback",
                "difficulty_applied": float(topic.get("difficulty", 0.60) or 0.60) * 100,
            }],
        }
    return None


def expected_correctness(mastery: float, difficulty_score: float) -> float:
    difficulty = max(0.0, min(1.0, difficulty_score / 100))
    return clamp(0.20 + 0.65 * mastery + 0.25 * (1.0 - difficulty), 0.12, 0.94)


def mastery_delta(mastery: float, correctness: float, difficulty_score: float, evidence_quality: float) -> float:
    difficulty = max(0.0, min(1.0, difficulty_score / 100))
    expected = expected_correctness(mastery, difficulty_score)
    surprise = correctness - expected
    difficulty_weight = 0.85 + 0.65 * difficulty
    if surprise >= 0:
        stability = 1.0 - 0.55 * mastery
        cap = 0.085
    else:
        stability = 0.55 + 0.45 * mastery
        cap = 0.075
    delta = 0.10 * difficulty_weight * surprise * stability * evidence_quality
    return max(-cap, min(cap, delta))


def apply_mastery_contribution(
    record: dict[str, Any],
    module: dict[str, Any],
    topic: dict[str, Any],
) -> dict[str, Any] | None:
    observation = topic_observation_from_record(record, topic)
    if not observation:
        return None
    old_mastery = clamp(topic.get("mastery", 0.0))
    correctness = clamp(observation["correctness"])
    difficulty_score = max(20.0, min(95.0, float(observation["difficulty_score"])))
    quality = record_source_quality(record)
    expected = expected_correctness(old_mastery, difficulty_score)
    delta = mastery_delta(old_mastery, correctness, difficulty_score, quality)
    if abs(delta) < 0.001:
        return None
    new_mastery = clamp(old_mastery + delta)
    topic["mastery"] = round(new_mastery, 4)
    topic["forgetting_risk"] = round(max(0.0, min(1.0, float(topic.get("forgetting_risk", 0.0) or 0.0) - max(delta, 0) * 0.45)), 4)
    source = topic.get("source_json")
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except json.JSONDecodeError:
            source = {}
    if not isinstance(source, dict):
        source = {}
    source["last_mastery_update"] = {
        "record_id": record.get("id"),
        "date": record.get("date"),
        "score": record.get("score"),
        "correctness": round(correctness, 4),
        "difficulty_score": round(difficulty_score, 1),
        "expected_correctness": round(expected, 4),
        "delta": round(delta, 4),
        "old_mastery": round(old_mastery, 4),
        "new_mastery": round(new_mastery, 4),
        "evidence_quality": quality,
    }
    topic["source_json"] = source
    return {
        "module": module.get("name"),
        "topic": topic.get("name"),
        "old_mastery": round(old_mastery * 100, 1),
        "new_mastery": round(new_mastery * 100, 1),
        "delta": round(delta * 100, 2),
        "correctness": round(correctness * 100, 1),
        "difficulty_score": round(difficulty_score, 1),
        "expected_correctness": round(expected * 100, 1),
        "evidence_quality": quality,
        "observation_count": int(observation.get("observation_count", 1)),
        "_trace_evidence_items": observation.get("evidence_items", []),
    }


def recompute_module_mastery(module: dict[str, Any]) -> None:
    topics = [topic for topic in module.get("topics", []) if isinstance(topic.get("mastery"), (int, float))]
    if topics:
        module["mastery"] = round(sum(float(topic["mastery"]) for topic in topics) / len(topics), 4)


def recompute_subject_mastery(subject: dict[str, Any]) -> None:
    modules = [
        module
        for module in subject.get("modules", [])
        if isinstance(module.get("mastery"), (int, float))
    ]
    if not modules:
        return
    total_weight = sum(float(module.get("weight", 1) or 1) for module in modules) or len(modules)
    subject["mastery"] = round(
        sum(float(module.get("mastery", 0) or 0) * float(module.get("weight", 1) or 1) for module in modules)
        / total_weight,
        4,
    )


def sync_topic_statuses_to_sqlite(
    subject_name: str,
    changed: list[tuple[str, str]],
    model: dict[str, Any],
    db_path: Path | str,
    *,
    connection: Any = None,
) -> None:
    if connection is not None:
        _sync_topic_statuses(connection, subject_name, changed, model)
        return
    with connect(db_path) as connection:
        _sync_topic_statuses(connection, subject_name, changed, model)


def _sync_topic_statuses(
    connection: Any,
    subject_name: str,
    changed: list[tuple[str, str]],
    model: dict[str, Any],
) -> None:
    model_topics: dict[tuple[str, str], dict[str, Any]] = {}
    model_modules: dict[str, dict[str, Any]] = {}
    for subject in model.get("subjects", []):
        if subject.get("name") != subject_name:
            continue
        for module in subject.get("modules", []):
            model_modules[str(module.get("name") or "")] = module
            for topic in module.get("topics", []):
                model_topics[(str(module.get("name") or ""), str(topic.get("name") or ""))] = topic

    for module_name, topic_name in changed:
        topic = model_topics.get((module_name, topic_name), {})
        connection.execute(
            """
            UPDATE topics
            SET status = ?, mastery = ?, difficulty = ?, forgetting_risk = ?, source_json = ?
            WHERE name = ?
              AND module_id IN (
                  SELECT modules.id
                  FROM modules
                  JOIN subjects ON subjects.id = modules.subject_id
                  WHERE subjects.name = ? AND modules.name = ?
              )
            """,
            (
                topic.get("status") or LEARNED_STATUS,
                float(topic.get("mastery", 0) or 0),
                float(topic.get("difficulty", 0) or 0),
                float(topic.get("forgetting_risk", 0) or 0),
                dumps(topic),
                topic_name,
                subject_name,
                module_name,
            ),
        )
    changed_modules = {module_name for module_name, _topic_name in changed}
    for module_name in changed_modules:
        connection.execute(
            """
            UPDATE modules
            SET status = ?, mastery = COALESCE(
                (
                    SELECT AVG(topics.mastery)
                    FROM topics
                    WHERE topics.module_id = modules.id
                ),
                mastery
            )
            WHERE name = ?
              AND subject_id = (SELECT id FROM subjects WHERE name = ?)
            """,
            (model_modules.get(module_name, {}).get("status"),
             module_name, subject_name),
        )
    connection.execute(
        """
        UPDATE subjects
        SET mastery = COALESCE(
            (
                SELECT SUM(modules.mastery * modules.weight) / NULLIF(SUM(modules.weight), 0)
                FROM modules
                WHERE modules.subject_id = subjects.id
            ),
            mastery
        )
        WHERE name = ?
        """,
        (subject_name,),
    )


def sync_existing_learning_records(
    model_path: Path | str = MODEL_PATH,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, list[str]]:
    from study_app.data.database import load_raw_records_from_connection

    path = ensure_seeded_database(db_path, model_path=model_path)
    with connect(path) as connection:
        records = load_raw_records_from_connection(connection)
    result: dict[str, list[str]] = {}
    for record in records:
        changed = sync_learning_record_to_model(record, model_path=model_path, db_path=db_path)
        if changed:
            result.setdefault(str(record.get("subject") or ""), []).extend(changed)
    return {subject: list(dict.fromkeys(topics)) for subject, topics in result.items()}


def sync_existing_classroom_records(
    model_path: Path | str = MODEL_PATH,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, list[str]]:
    """Compatibility wrapper; existing records now include all real learning evidence."""
    return sync_existing_learning_records(model_path=model_path, db_path=db_path)
