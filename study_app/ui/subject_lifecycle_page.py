from __future__ import annotations

import json
from pathlib import Path

from study_app.data.database import DEFAULT_DB_PATH
from study_app.data.subject_repository import SubjectCatalogRepository


def load_subject_lifecycle_view(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    actor_role: str = "viewer",
) -> dict[str, object]:
    """Load one DB-revision-bound lifecycle view without mutating state."""
    if actor_role not in {"viewer", "reviewer", "executor", "owner"}:
        raise ValueError("未知生命周期界面角色")
    repository = SubjectCatalogRepository(db_path)
    with repository._open() as connection:
        revision = int(
            connection.execute(
                "SELECT catalog_revision FROM subject_catalog_state WHERE singleton=1"
            ).fetchone()[0]
        )
        subjects = [
            dict(row)
            for row in connection.execute(
                """
                SELECT subject_key,canonical_name,display_name,lifecycle_status,object_version
                FROM subject_catalog ORDER BY canonical_name_normalized
                """
            ).fetchall()
        ]
        documents = [
            dict(row)
            for row in connection.execute(
                """
                SELECT documents.document_hash,documents.original_name,documents.page_count,
                       parses.parse_key,parses.status AS parse_status
                FROM subject_documents documents
                LEFT JOIN document_parses parses ON parses.document_hash=documents.document_hash
                ORDER BY documents.registered_at DESC,parses.created_at DESC
                """
            ).fetchall()
        ]
        evidence = [
            dict(row)
            for row in connection.execute(
                """
                SELECT evidence_id,parse_key,physical_page,page_label,printed_page,
                       evidence_type,quality_status
                FROM subject_evidence
                ORDER BY created_at DESC,evidence_id LIMIT 50
                """
            ).fetchall()
        ]
        manifests = [
            dict(row)
            for row in connection.execute(
                """
                SELECT manifest_version,schema_version,generator_version,payload_hash,status,
                       object_version,created_at
                FROM subject_manifest_versions ORDER BY created_at DESC,manifest_version
                """
            ).fetchall()
        ]
        operations = []
        for row in connection.execute(
            """
            SELECT operations.operation_id,operations.changeset_hash,operations.status,
                   operations.expected_catalog_revision,operations.updated_at,
                   changesets.payload_json,approvals.approver,approvals.approved_at,
                   outbox.task_id AS projection_task_id,outbox.status AS projection_status,
                   outbox.last_error AS projection_error
            FROM subject_change_operations operations
            JOIN subject_changesets changesets ON changesets.operation_id=operations.operation_id
            LEFT JOIN subject_approvals approvals ON approvals.operation_id=operations.operation_id
            LEFT JOIN subject_projection_outbox outbox ON outbox.operation_id=operations.operation_id
            ORDER BY operations.created_at DESC,operations.operation_id
            """
        ).fetchall():
            item = dict(row)
            payload = json.loads(item.pop("payload_json"))
            item["expected_diff"] = payload.get("expected_diff", {})
            status = item["status"]
            item["can_approve"] = actor_role in {"reviewer", "owner"} and status == "prepared"
            item["can_execute"] = actor_role in {"executor", "owner"} and status == "approved"
            item["can_recover"] = (
                actor_role in {"executor", "owner"}
                and status == "db_committed_projection_pending"
                and item.get("projection_status") in {"pending", "failed"}
            )
            operations.append(item)
        events = [
            dict(row)
            for row in connection.execute(
                """
                SELECT operation_id,event_type,detail_json,created_at
                FROM subject_operation_events ORDER BY event_id DESC LIMIT 100
                """
            ).fetchall()
        ]
    return {
        "catalog_revision": revision,
        "subjects": subjects,
        "documents": documents,
        "evidence": evidence,
        "manifests": manifests,
        "operations": operations,
        "events": events,
        "actor_role": actor_role,
    }


def subject_lifecycle_page(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    actor_role: str = "owner",
):
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    layout = QVBoxLayout(content)
    title = QLabel("学科生命周期")
    title.setObjectName("HeroTitle")
    layout.addWidget(title)

    def clear_rows() -> None:
        while layout.count() > 1:
            item = layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def action(operation: dict, kind: str) -> None:
        try:
            if kind == "approve":
                from study_app.core.subject_changeset import approve_changeset

                approve_changeset(db_path, operation["operation_id"], approver=actor_role)
            elif kind == "execute":
                from study_app.core.subject_executor import execute_switch_changeset

                execute_switch_changeset(
                    db_path, operation["operation_id"], actor=actor_role
                )
            elif kind == "recover":
                from study_app.core.subject_projection import process_projection_outbox

                projection_path = Path(db_path).with_name("subject_catalog_projection.json")
                process_projection_outbox(
                    db_path,
                    projection_path,
                    task_id=operation["projection_task_id"],
                )
            refresh()
        except Exception as error:
            QMessageBox.warning(content, "生命周期操作失败", str(error))

    def section(label: str, lines: list[str]) -> None:
        heading = QLabel(label)
        heading.setObjectName("CardTitle")
        layout.addWidget(heading)
        body = QLabel("\n".join(lines) if lines else "暂无")
        body.setWordWrap(True)
        body.setObjectName("Muted")
        layout.addWidget(body)

    def refresh() -> None:
        clear_rows()
        try:
            view = load_subject_lifecycle_view(db_path, actor_role=actor_role)
        except Exception as error:
            section("状态", [f"F5 生命周期结构不可用：{error}"])
            return
        section(
            f"目录修订 {view['catalog_revision']}",
            [
                f"{item['display_name']} · {item['lifecycle_status']} · v{item['object_version']}"
                for item in view["subjects"]
            ],
        )
        section(
            "教材与解析",
            [
                f"{item['original_name']} · pages={item['page_count']} · {item.get('parse_status') or '未解析'}"
                for item in view["documents"]
            ],
        )
        section(
            "证据定位",
            [
                f"{item['evidence_id']} · physical={item['physical_page']} · label={item.get('page_label') or '-'} · {item['quality_status']}"
                for item in view["evidence"]
            ],
        )
        section(
            "候选结构与签核",
            [
                f"{item['manifest_version']} · {item['status']} · {item['payload_hash'][:12]}"
                for item in view["manifests"]
            ],
        )
        operations_heading = QLabel("差异、执行与恢复")
        operations_heading.setObjectName("CardTitle")
        layout.addWidget(operations_heading)
        for operation in view["operations"]:
            row = QWidget()
            row_layout = QVBoxLayout(row)
            detail = QLabel(
                f"{operation['operation_id']} · {operation['status']} · "
                f"diff={json.dumps(operation['expected_diff'], ensure_ascii=False, sort_keys=True)} · "
                f"projection={operation.get('projection_status') or '-'}"
            )
            detail.setWordWrap(True)
            row_layout.addWidget(detail)
            buttons = QHBoxLayout()
            for kind, label, enabled_key in (
                ("approve", "签核", "can_approve"),
                ("execute", "执行", "can_execute"),
                ("recover", "恢复投影", "can_recover"),
            ):
                button = QPushButton(label)
                button.setEnabled(bool(operation[enabled_key]))
                button.clicked.connect(
                    lambda _checked=False, op=operation, selected=kind: action(op, selected)
                )
                buttons.addWidget(button)
            buttons.addStretch()
            row_layout.addLayout(buttons)
            layout.addWidget(row)
        section(
            "操作审计",
            [
                f"{item['created_at']} · {item['operation_id']} · {item['event_type']}"
                for item in view["events"]
            ],
        )
        layout.addStretch()

    refresh()
    scroll._refresh_subject_lifecycle = refresh
    scroll.setWidget(content)
    return scroll
