from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class AIAuditDiagnosticsTests(unittest.TestCase):
    @staticmethod
    def settings():
        return SimpleNamespace(provider="provider", model="model")

    def test_audit_insert_failure_blocks_llm_request(self) -> None:
        from study_app.ai.audit import AuditPersistenceError, audited_chat_completion_json

        with (
            patch("study_app.ai.audit.load_llm_settings", return_value=self.settings()),
            patch("study_app.ai.audit.estimate_tokens", return_value=12),
            patch("study_app.ai.audit.assert_can_call_llm", side_effect=lambda _p, s: s),
            patch("study_app.ai.audit.chat_completion_json", return_value='{"ok":true}') as chat_call,
            patch("study_app.ai.audit.record_llm_call_audit", side_effect=OSError("audit disk full")),
            self.assertLogs("study_app.ai.audit", level="ERROR") as captured,
        ):
            with self.assertRaises(AuditPersistenceError):
                audited_chat_completion_json("plan", "prompt")

        chat_call.assert_not_called()
        self.assertIn("OSError", "\n".join(captured.output))

    def test_audit_update_failure_blocks_provider_failure_completion(self) -> None:
        from study_app.ai.audit import AuditPersistenceError, audited_chat_completion_json

        provider_error = RuntimeError("PRIVATE_PROVIDER_DETAIL_123")
        with (
            patch("study_app.ai.audit.load_llm_settings", return_value=self.settings()),
            patch("study_app.ai.audit.estimate_tokens", return_value=12),
            patch("study_app.ai.audit.assert_can_call_llm", side_effect=lambda _p, s: s),
            patch(
                "study_app.ai.audit.chat_completion_json",
                side_effect=provider_error,
            ) as provider_call,
            patch(
                "study_app.ai.audit.record_llm_call_audit",
                return_value=7,
            ) as audit_call,
            patch(
                "study_app.ai.audit.update_llm_call_audit",
                side_effect=OSError("audit disk full"),
            ) as update_call,
            self.assertLogs("study_app.ai.audit", level="ERROR") as logs,
        ):
            with self.assertRaises(AuditPersistenceError):
                audited_chat_completion_json("plan", "PRIVATE_PROMPT_456")

        diagnostic = "\n".join(logs.output)
        self.assertIn("OSError", diagnostic)
        self.assertNotIn("PRIVATE_PROVIDER_DETAIL_123", diagnostic)
        self.assertNotIn("PRIVATE_PROMPT_456", diagnostic)
        self.assertNotIn("PRIVATE_RESPONSE_789", diagnostic)
        provider_call.assert_called_once()
        audit_call.assert_called_once()
        update_call.assert_called_once()

    def test_failed_llm_audit_does_not_persist_provider_error_detail(self) -> None:
        from study_app.ai.audit import audited_chat_completion_json

        provider_error = RuntimeError("PRIVATE_PROVIDER_RESPONSE_DETAIL")
        with (
            patch("study_app.ai.audit.load_llm_settings", return_value=self.settings()),
            patch("study_app.ai.audit.estimate_tokens", return_value=12),
            patch("study_app.ai.audit.assert_can_call_llm", side_effect=lambda _p, s: s),
            patch(
                "study_app.ai.audit.chat_completion_json",
                side_effect=provider_error,
            ),
            patch("study_app.ai.audit.record_llm_call_audit", return_value=7) as audit_call,
            patch("study_app.ai.audit.update_llm_call_audit") as update_call,
        ):
            with self.assertRaises(RuntimeError) as captured:
                audited_chat_completion_json("plan", "PRIVATE_PROMPT_DETAIL")

        self.assertIs(captured.exception, provider_error)
        fields = audit_call.call_args.kwargs
        self.assertEqual(fields["status"], "requested")
        self.assertEqual(update_call.call_args.args[1], "transport_failed")
        self.assertEqual(update_call.call_args.args[2], "RuntimeError")
        self.assertNotIn("PRIVATE_PROVIDER_RESPONSE_DETAIL", str(fields))
        self.assertNotIn("PRIVATE_PROMPT_DETAIL", str(fields))

    def test_audit_boundary_rejects_arbitrary_summary_strings_and_keys(self) -> None:
        from study_app.ai.audit import audited_chat_completion_json

        with (
            patch("study_app.ai.audit.load_llm_settings", return_value=self.settings()),
            patch("study_app.ai.audit.estimate_tokens", return_value=12),
            patch("study_app.ai.audit.assert_can_call_llm", side_effect=lambda _p, s: s),
            patch("study_app.ai.audit.chat_completion_json", return_value='{"ok":true}'),
            patch("study_app.ai.audit.record_llm_call_audit", return_value=7) as audit_call,
            patch("study_app.ai.audit.update_llm_call_audit"),
        ):
            audited_chat_completion_json(
                "plan",
                "prompt",
                {
                    "repair_reason": "PRIVATE_MODEL_OUTPUT",
                    "PRIVATE_DYNAMIC_FIELD": {
                        "type": "object",
                        "keys": ["PRIVATE_NESTED_KEY"],
                    },
                },
            )

        summary = audit_call.call_args.kwargs["upload_summary"]
        self.assertEqual(summary, {})
        self.assertNotIn("PRIVATE_MODEL_OUTPUT", str(audit_call.call_args))
        self.assertNotIn("PRIVATE_DYNAMIC_FIELD", str(audit_call.call_args))
        self.assertNotIn("PRIVATE_NESTED_KEY", str(audit_call.call_args))

    def test_payload_summary_does_not_retain_nested_object_keys(self) -> None:
        from study_app.ai.audit import summarize_payload

        summary = summarize_payload(
            {
                "question": "PRIVATE_NATURAL_QUERY",
                "window": {
                    "PRIVATE_SUBJECT_KEY": "PRIVATE_VALUE",
                    "benchmark": 60,
                }
            }
        )

        self.assertEqual(
            summary,
            {
                "question": {"type": "text", "chars": 21},
                "window": {"type": "object", "items": 2},
            },
        )
        self.assertNotIn("PRIVATE_NATURAL_QUERY", str(summary))
        self.assertNotIn("PRIVATE_SUBJECT_KEY", str(summary))
        self.assertNotIn("PRIVATE_VALUE", str(summary))

    def test_sqlite_audit_rows_contain_metadata_only(self) -> None:
        from study_app.ai.audit import audited_chat_completion_json
        from study_app.data.database import connect, initialize_database, record_llm_call_audit

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite"
            initialize_database(db_path)

            def persist(**fields):
                return record_llm_call_audit(**fields, db_path=db_path)

            def update(audit_id, status, error_message=None):
                from study_app.data.database import update_llm_call_audit

                return update_llm_call_audit(audit_id, status, error_message, db_path=db_path)

            def call_llm(prompt, summary=None, **chat_result):
                with (
                    patch("study_app.ai.audit.load_llm_settings", return_value=self.settings()),
                    patch("study_app.ai.audit.estimate_tokens", return_value=12),
                    patch("study_app.ai.audit.assert_can_call_llm", side_effect=lambda _p, s: s),
                    patch("study_app.ai.audit.record_llm_call_audit", side_effect=persist),
                    patch("study_app.ai.audit.update_llm_call_audit", side_effect=update),
                    patch("study_app.data.database.ensure_seeded_database", return_value=db_path),
                    patch("study_app.ai.audit.chat_completion_json", **chat_result),
                ):
                    return audited_chat_completion_json("plan", prompt, summary)

            call_llm(
                "PRIVATE_PROMPT_SQLITE",
                {"repair_reason": "PRIVATE_MODEL_OUTPUT_SQLITE"},
                return_value='{"ok":true}',
            )
            with self.assertRaises(RuntimeError):
                call_llm(
                    "PRIVATE_PROMPT_SQLITE",
                    side_effect=RuntimeError("PRIVATE_PROVIDER_SQLITE"),
                )

            with connect(db_path) as connection:
                rows = connection.execute(
                    "SELECT upload_summary_json, status, error_message FROM llm_call_audits ORDER BY id"
                ).fetchall()

        persisted = str([tuple(row) for row in rows])
        self.assertEqual(len(rows), 2)
        self.assertNotIn("PRIVATE_", persisted)
        self.assertEqual(rows[0]["status"], "parsed_unvalidated")
        self.assertEqual(rows[1]["status"], "transport_failed")
        self.assertEqual(rows[1]["error_message"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
