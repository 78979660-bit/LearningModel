from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from study_app.core.subject_capabilities import CAPABILITY_KEYS
from study_app.core.subject_manifest import SubjectManifestCandidate
from study_app.data.subject_repository import SubjectCatalogRepository


VALIDATOR_VERSION = "subject-validator-v1"


@dataclass(frozen=True, order=True)
class ValidationIssue:
    path: str
    code: str
    message: str
    physical_page: int | None = None
    evidence_id: str | None = None


@dataclass(frozen=True)
class ValidationReport:
    validator_version: str
    valid: bool
    issues: tuple[ValidationIssue, ...]

    def require_valid(self) -> None:
        if not self.valid:
            first = self.issues[0]
            raise ValueError(f"Manifest 校验失败 {first.path}: {first.message}")


def _integer_bp(
    value: object,
    *,
    path: str,
    issues: list[ValidationIssue],
    required: bool = False,
) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10000:
        issues.append(
            ValidationIssue(path, "invalid_basis_points", "必须是 0-10000 的整数 basis points")
        )
        return None
    return value


def _detect_cycle(edges: dict[str, set[str]]) -> tuple[str, ...] | None:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> tuple[str, ...] | None:
        if node in visiting:
            start = stack.index(node)
            return tuple(stack[start:] + [node])
        if node in visited:
            return None
        visiting.add(node)
        stack.append(node)
        for target in sorted(edges.get(node, set())):
            cycle = visit(target)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return None

    for node in sorted(edges):
        cycle = visit(node)
        if cycle:
            return cycle
    return None


def validate_manifest(
    candidate: SubjectManifestCandidate,
    db_path: Path | str,
    parse_key: str,
) -> ValidationReport:
    payload = candidate.payload
    issues: list[ValidationIssue] = []
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction() as connection:
        evidence_rows = connection.execute(
            """
            SELECT evidence_id, physical_page, evidence_type, quality_status
            FROM subject_evidence WHERE parse_key = ? ORDER BY evidence_id
            """,
            (parse_key,),
        ).fetchall()
    evidence = {row["evidence_id"]: row for row in evidence_rows}

    for warning in candidate.planning_warnings:
        code = str(warning.get("code") or "planning_warning")
        if code in {"missing_evidence", "unknown_evidence_id"}:
            evidence_id = warning.get("evidence_id")
            issues.append(
                ValidationIssue(
                    str(warning.get("path") or "$"),
                    code,
                    "结构结论缺少有效教材证据",
                    evidence_id=str(evidence_id) if evidence_id else None,
                )
            )

    def require_evidence(references: object, path: str) -> tuple[str, ...]:
        if not isinstance(references, list) or not references:
            issues.append(ValidationIssue(path, "missing_evidence", "必须引用至少一条证据"))
            return ()
        result: list[str] = []
        for index, evidence_id in enumerate(references):
            item_path = f"{path}[{index}]"
            if not isinstance(evidence_id, str) or evidence_id not in evidence:
                issues.append(
                    ValidationIssue(
                        item_path,
                        "unknown_evidence",
                        "证据不存在于当前 parse_key",
                        evidence_id=str(evidence_id),
                    )
                )
                continue
            row = evidence[evidence_id]
            if row["quality_status"] != "success":
                issues.append(
                    ValidationIssue(
                        item_path,
                        "low_quality_evidence",
                        "非 success 证据不得支持正式结构",
                        int(row["physical_page"]),
                        evidence_id,
                    )
                )
            result.append(evidence_id)
        return tuple(result)

    subject = payload.get("subject", {})
    if not isinstance(subject.get("name"), str) or not subject["name"].strip():
        issues.append(ValidationIssue("subject.name", "invalid_name", "学科名称不能为空"))
    forbidden_keys = {"subject_key", "module_key", "topic_key"}
    for key in sorted(forbidden_keys.intersection(payload) | forbidden_keys.intersection(subject)):
        issues.append(ValidationIssue(key, "formal_identity_forbidden", "LLM 不得分配正式身份"))

    modules = payload.get("modules", [])
    all_ids: set[str] = set()
    topic_ids: set[str] = set()
    module_weight_values: list[int] = []
    for module_index, module in enumerate(modules):
        module_path = f"modules[{module_index}]"
        module_id = module.get("candidate_id") if isinstance(module, dict) else None
        if not isinstance(module_id, str):
            issues.append(ValidationIssue(module_path + ".candidate_id", "missing_candidate_id", "缺少候选标识"))
            continue
        if module_id in all_ids:
            issues.append(ValidationIssue(module_path + ".candidate_id", "duplicate_candidate_id", "候选标识重复"))
        all_ids.add(module_id)
        if not isinstance(module.get("name"), str) or not module["name"].strip():
            issues.append(ValidationIssue(module_path + ".name", "invalid_name", "模块名称不能为空"))
        module_weight = _integer_bp(module.get("weight_bp"), path=module_path + ".weight_bp", issues=issues)
        if module_weight is not None:
            module_weight_values.append(module_weight)
        require_evidence(module.get("evidence_ids"), module_path + ".evidence_ids")
        topic_weight_values: list[int] = []
        for topic_index, topic in enumerate(module.get("topics", [])):
            topic_path = f"{module_path}.topics[{topic_index}]"
            topic_id = topic.get("candidate_id") if isinstance(topic, dict) else None
            if not isinstance(topic_id, str):
                issues.append(ValidationIssue(topic_path + ".candidate_id", "missing_candidate_id", "缺少候选标识"))
                continue
            if topic_id in all_ids:
                issues.append(ValidationIssue(topic_path + ".candidate_id", "duplicate_candidate_id", "候选标识重复"))
            all_ids.add(topic_id)
            topic_ids.add(topic_id)
            if not isinstance(topic.get("name"), str) or not topic["name"].strip():
                issues.append(ValidationIssue(topic_path + ".name", "invalid_name", "知识点名称不能为空"))
            topic_weight = _integer_bp(topic.get("weight_bp"), path=topic_path + ".weight_bp", issues=issues)
            if topic_weight is not None:
                topic_weight_values.append(topic_weight)
            _integer_bp(topic.get("importance_bp"), path=topic_path + ".importance_bp", issues=issues)
            _integer_bp(topic.get("difficulty_bp"), path=topic_path + ".difficulty_bp", issues=issues)
            require_evidence(topic.get("evidence_ids"), topic_path + ".evidence_ids")
        if topic_weight_values and sum(topic_weight_values) != 10000:
            issues.append(ValidationIssue(module_path + ".topics", "topic_weight_sum", "知识点权重之和必须为 10000"))
    if module_weight_values and sum(module_weight_values) != 10000:
        issues.append(ValidationIssue("modules", "module_weight_sum", "模块权重之和必须为 10000"))

    edges: dict[str, set[str]] = {topic_id: set() for topic_id in topic_ids}
    for index, edge in enumerate(payload.get("prerequisites", [])):
        path = f"prerequisites[{index}]"
        if not isinstance(edge, dict):
            issues.append(ValidationIssue(path, "invalid_edge", "先修边必须是对象"))
            continue
        source = edge.get("from_candidate_id")
        target = edge.get("to_candidate_id")
        if source not in topic_ids or target not in topic_ids:
            issues.append(ValidationIssue(path, "unknown_prerequisite_node", "先修边引用未知知识点"))
            continue
        if source == target:
            issues.append(ValidationIssue(path, "self_cycle", "先修边不得自环"))
        edges[source].add(target)
        refs = require_evidence(edge.get("evidence_ids"), path + ".evidence_ids")
        if edge.get("evidence_type") == "llm_inference" and edge.get("confirmed") is True:
            first_ref = evidence.get(refs[0]) if refs else None
            issues.append(
                ValidationIssue(
                    path + ".confirmed",
                    "inference_requires_decision",
                    "LLM 推断先修关系未经 user_decision 不得确认",
                    int(first_ref["physical_page"]) if first_ref else None,
                    refs[0] if refs else None,
                )
            )
    cycle = _detect_cycle(edges)
    if cycle:
        issues.append(ValidationIssue("prerequisites", "prerequisite_cycle", "先修图存在循环：" + " -> ".join(cycle)))

    capabilities = payload.get("capabilities", {})
    if not isinstance(capabilities, dict):
        issues.append(ValidationIssue("capabilities", "invalid_capabilities", "能力声明必须是对象"))
    else:
        for key in sorted(capabilities):
            if key not in CAPABILITY_KEYS:
                issues.append(ValidationIssue(f"capabilities.{key}", "unknown_capability", "未知能力键"))
            elif type(capabilities[key]) is not bool:
                issues.append(ValidationIssue(f"capabilities.{key}", "invalid_capability_value", "能力值必须是布尔值"))

    for index, mapping in enumerate(payload.get("mapping_candidates", [])):
        path = f"mapping_candidates[{index}]"
        if not isinstance(mapping, dict):
            issues.append(ValidationIssue(path, "invalid_mapping", "映射必须是对象"))
            continue
        if mapping.get("mapping_type") not in {"equivalent", "partial", "none"}:
            issues.append(ValidationIssue(path + ".mapping_type", "invalid_mapping_type", "映射类型无效"))
        _integer_bp(mapping.get("confidence_bp"), path=path + ".confidence_bp", issues=issues, required=True)
        require_evidence(mapping.get("evidence_ids"), path + ".evidence_ids")

    unique_issues = tuple(sorted(set(issues)))
    return ValidationReport(VALIDATOR_VERSION, not unique_issues, unique_issues)
