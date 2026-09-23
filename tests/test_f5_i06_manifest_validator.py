from __future__ import annotations

import json
import sqlite3

from study_app.core.subject_manifest import parse_manifest_candidate
from study_app.core.subject_validator import VALIDATOR_VERSION, validate_manifest
from study_app.data.database import initialize_database
from study_app.data.subject_repository import install_subject_lifecycle_schema


def setup_case(tmp_path, *, quality="success"):
    db = tmp_path / "validator.sqlite"
    initialize_database(db)
    install_subject_lifecycle_schema(db)
    doc = "a" * 64
    parse = "b" * 64
    evidence = "c" * 64
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO subject_documents(document_hash,original_name,file_path,mime_type,byte_size,page_count,encrypted,file_version,preflight_status) VALUES (?, 'b.pdf','b.pdf','application/pdf',1,1,0,'v1','ready')",
            (doc,),
        )
        connection.execute(
            "INSERT INTO document_parses(parse_key,document_hash,parser_name,parser_version,config_hash,config_json,status,page_count) VALUES (?,?, 'fixture','1',?,'{}','completed',1)",
            (parse, doc, "d" * 64),
        )
        connection.execute(
            "INSERT INTO document_pages(parse_key,physical_page,status) VALUES (?,1,?)",
            (parse, quality),
        )
        connection.execute(
            """
            INSERT INTO subject_evidence(
                evidence_id,parse_key,physical_page,page_label,bbox_json,rotation,
                coordinate_version,object_type,evidence_type,excerpt,excerpt_hash,quality_status
            ) VALUES (?,?,1,'1','[0,0,1,1]',0,'v1','paragraph','explicit_source','source text',? ,?)
            """,
            (evidence, parse, "e" * 64, quality),
        )
    return db, parse, evidence


def valid_payload(evidence):
    return {
        "schema_version": "subject-manifest-v1",
        "candidate_id": "subject_candidate",
        "subject": {"name": "物理"},
        "modules": [
            {
                "candidate_id": "module_a",
                "name": "力学",
                "weight_bp": 10000,
                "evidence_ids": [evidence],
                "topics": [
                    {"candidate_id": "topic_a", "name": "动量", "weight_bp": 5000, "importance_bp": 8000, "difficulty_bp": 6000, "evidence_ids": [evidence]},
                    {"candidate_id": "topic_b", "name": "能量", "weight_bp": 5000, "importance_bp": 7000, "difficulty_bp": 6500, "evidence_ids": [evidence]},
                ],
            }
        ],
        "prerequisites": [
            {"from_candidate_id": "topic_a", "to_candidate_id": "topic_b", "evidence_ids": [evidence], "evidence_type": "explicit_source", "confirmed": True}
        ],
        "capabilities": {"study_plan": True, "oj": False},
        "mapping_candidates": [
            {"mapping_type": "partial", "confidence_bp": 7000, "evidence_ids": [evidence]}
        ],
        "uncertainties": [],
        "decisions": [],
    }


def candidate(payload, evidence):
    return parse_manifest_candidate(json.dumps(payload, ensure_ascii=False), evidence_index={evidence: (1, "1")})


def test_valid_manifest_is_deterministic(tmp_path):
    db, parse, evidence = setup_case(tmp_path)
    item = candidate(valid_payload(evidence), evidence)
    first = validate_manifest(item, db, parse)
    second = validate_manifest(item, db, parse)
    assert first == second
    assert first.valid is True
    assert first.validator_version == VALIDATOR_VERSION
    first.require_valid()


def test_invalid_hierarchy_weights_capabilities_and_mapping_are_located(tmp_path):
    db, parse, evidence = setup_case(tmp_path)
    payload = valid_payload(evidence)
    payload["modules"][0]["weight_bp"] = 9000
    payload["modules"][0]["topics"][0]["weight_bp"] = 9001
    payload["capabilities"]["teleport"] = True
    payload["capabilities"]["study_plan"] = "yes"
    payload["mapping_candidates"][0]["mapping_type"] = "similar"
    payload["mapping_candidates"][0]["confidence_bp"] = 20000
    report = validate_manifest(candidate(payload, evidence), db, parse)
    codes = {issue.code for issue in report.issues}
    assert {"module_weight_sum", "topic_weight_sum", "unknown_capability", "invalid_capability_value", "invalid_mapping_type", "invalid_basis_points"}.issubset(codes)
    assert all(issue.path for issue in report.issues)


def test_unknown_and_low_quality_evidence_are_rejected_with_page(tmp_path):
    db, parse, evidence = setup_case(tmp_path, quality="needs_review")
    payload = valid_payload(evidence)
    payload["modules"][0]["topics"][0]["evidence_ids"] = ["f" * 64]
    report = validate_manifest(candidate(payload, evidence), db, parse)
    assert report.valid is False
    assert any(issue.code == "unknown_evidence" for issue in report.issues)
    low = [issue for issue in report.issues if issue.code == "low_quality_evidence"]
    assert low and all(issue.physical_page == 1 for issue in low)


def test_self_loop_and_cycle_are_rejected(tmp_path):
    db, parse, evidence = setup_case(tmp_path)
    payload = valid_payload(evidence)
    payload["prerequisites"] = [
        {"from_candidate_id": "topic_a", "to_candidate_id": "topic_a", "evidence_ids": [evidence]},
        {"from_candidate_id": "topic_a", "to_candidate_id": "topic_b", "evidence_ids": [evidence]},
        {"from_candidate_id": "topic_b", "to_candidate_id": "topic_a", "evidence_ids": [evidence]},
    ]
    report = validate_manifest(candidate(payload, evidence), db, parse)
    codes = {issue.code for issue in report.issues}
    assert "self_cycle" in codes
    assert "prerequisite_cycle" in codes


def test_llm_inference_cannot_be_confirmed_without_user_decision(tmp_path):
    db, parse, evidence = setup_case(tmp_path)
    payload = valid_payload(evidence)
    payload["prerequisites"][0]["evidence_type"] = "llm_inference"
    report = validate_manifest(candidate(payload, evidence), db, parse)
    issue = next(item for item in report.issues if item.code == "inference_requires_decision")
    assert issue.path == "prerequisites[0].confirmed"
    assert issue.physical_page == 1


def test_missing_evidence_warning_becomes_hard_error(tmp_path):
    db, parse, evidence = setup_case(tmp_path)
    payload = valid_payload(evidence)
    payload["modules"][0]["topics"][0]["evidence_ids"] = []
    item = candidate(payload, evidence)
    report = validate_manifest(item, db, parse)
    assert report.valid is False
    assert any(issue.code == "missing_evidence" for issue in report.issues)
