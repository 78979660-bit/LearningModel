from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping


MANIFEST_SCHEMA_VERSION = "subject-manifest-v1"
_CANDIDATE_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")


class ManifestShapeError(ValueError):
    pass


def validate_candidate_id(value: object, *, label: str = "candidate_id") -> str:
    if not isinstance(value, str) or not _CANDIDATE_ID.fullmatch(value):
        raise ManifestShapeError(f"{label} 格式无效：{value!r}")
    return value


@dataclass(frozen=True)
class SubjectManifestCandidate:
    payload: dict
    planning_warnings: tuple[dict[str, object], ...]

    @property
    def candidate_id(self) -> str:
        return str(self.payload["candidate_id"])

    @property
    def uncertainties(self) -> tuple[object, ...]:
        return tuple(self.payload.get("uncertainties", []))


def parse_manifest_candidate(
    raw: str,
    *,
    evidence_index: Mapping[str, tuple[int, str]],
) -> SubjectManifestCandidate:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ManifestShapeError(f"候选不是合法 JSON：{error.msg}") from error
    if not isinstance(payload, dict):
        raise ManifestShapeError("候选顶层必须是对象")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ManifestShapeError("不支持的 Manifest schema_version")
    validate_candidate_id(payload.get("candidate_id"))
    subject = payload.get("subject")
    if not isinstance(subject, dict) or not isinstance(subject.get("name"), str):
        raise ManifestShapeError("subject.name 必须存在")
    for forbidden in ("subject_key", "module_key", "topic_key"):
        if forbidden in payload or forbidden in subject:
            raise ManifestShapeError(f"LLM 不得分配正式身份字段：{forbidden}")
    modules = payload.get("modules")
    if not isinstance(modules, list):
        raise ManifestShapeError("modules 必须是数组")
    warnings: list[dict[str, object]] = []
    candidate_ids: set[str] = set()
    for module_index, module in enumerate(modules):
        if not isinstance(module, dict):
            raise ManifestShapeError(f"modules[{module_index}] 必须是对象")
        module_id = validate_candidate_id(
            module.get("candidate_id"), label=f"modules[{module_index}].candidate_id"
        )
        if module_id in candidate_ids:
            raise ManifestShapeError(f"候选局部标识重复：{module_id}")
        candidate_ids.add(module_id)
        topics = module.get("topics")
        if not isinstance(topics, list):
            raise ManifestShapeError(f"modules[{module_index}].topics 必须是数组")
        for topic_index, topic in enumerate(topics):
            if not isinstance(topic, dict):
                raise ManifestShapeError(
                    f"modules[{module_index}].topics[{topic_index}] 必须是对象"
                )
            topic_id = validate_candidate_id(
                topic.get("candidate_id"),
                label=f"modules[{module_index}].topics[{topic_index}].candidate_id",
            )
            if topic_id in candidate_ids:
                raise ManifestShapeError(f"候选局部标识重复：{topic_id}")
            candidate_ids.add(topic_id)
            references = topic.get("evidence_ids", [])
            if not references:
                warnings.append(
                    {
                        "code": "missing_evidence",
                        "path": f"modules[{module_index}].topics[{topic_index}]",
                    }
                )
            elif not isinstance(references, list):
                raise ManifestShapeError("evidence_ids 必须是数组")
            else:
                for evidence_id in references:
                    if evidence_id not in evidence_index:
                        warnings.append(
                            {
                                "code": "unknown_evidence_id",
                                "path": f"modules[{module_index}].topics[{topic_index}]",
                                "evidence_id": evidence_id,
                            }
                        )
    for field in (
        "prerequisites",
        "capabilities",
        "mapping_candidates",
        "uncertainties",
        "decisions",
    ):
        if field not in payload:
            warnings.append({"code": "missing_optional_section", "path": field})
    return SubjectManifestCandidate(payload=payload, planning_warnings=tuple(warnings))
