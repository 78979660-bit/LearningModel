from __future__ import annotations

from data_test_support import IsolatedDatabaseTestCase
from study_app.data import database


class DataSettingsAuditTests(IsolatedDatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.initialize_seed_guarded_database()

    def test_setting_insert_update_and_unicode_json_round_trip(self) -> None:
        self.assertEqual(database.dumps({"标题": "复习", "项": [1, 2]}), '{"标题":"复习","项":[1,2]}')

        database.set_setting("ui.state", {"模式": "本地"}, self.db_path)
        database.set_setting("ui.state", {"模式": "增强", "启用": True}, self.db_path)

        self.assertEqual(
            database.get_setting("ui.state", db_path=self.db_path),
            {"模式": "增强", "启用": True},
        )

    def test_get_setting_returns_default_for_missing_or_malformed_json(self) -> None:
        marker = {"fallback": True}
        self.assertIs(database.get_setting("missing", marker, self.db_path), marker)

        with database.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO app_settings(key, value_json) VALUES (?, ?)",
                ("broken", "{not-json"),
            )
        self.assertIs(database.get_setting("broken", marker, self.db_path), marker)

    def test_delete_settings_prefix_treats_percent_and_underscore_literally(self) -> None:
        keys = ("cache_%alpha", "cache_%beta", "cache_Xalpha", "cache-other")
        for key in keys:
            database.set_setting(key, key, self.db_path)

        deleted = database.delete_settings_by_prefix("cache_%", self.db_path)

        with database.connect(self.db_path) as connection:
            remaining = {
                row["key"] for row in connection.execute("SELECT key FROM app_settings")
            }
        self.assertEqual(deleted, 2)
        self.assertTrue({"cache_Xalpha", "cache-other"}.issubset(remaining))
        self.assertFalse({"cache_%alpha", "cache_%beta"} & remaining)

    def test_llm_audit_round_trip_order_limit_defaults_and_malformed_summary(self) -> None:
        database.record_llm_call_audit(
            "summary",
            "provider-a",
            "model-a",
            15,
            {"images": 2},
            "success",
            db_path=self.db_path,
        )
        database.record_llm_call_audit(
            "plan",
            "provider-b",
            "model-b",
            0,
            {},
            "failed",
            "timeout",
            self.db_path,
        )
        with database.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE llm_call_audits SET upload_summary_json = ? WHERE feature = ?",
                ("{broken", "plan"),
            )

        rows = database.list_llm_call_audits(limit=1, db_path=self.db_path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["feature"], "plan")
        self.assertEqual(rows[0]["estimated_tokens"], 0)
        self.assertEqual(rows[0]["upload_summary"], {})
        self.assertEqual(rows[0]["error_message"], "timeout")
        self.assertNotIn("upload_summary_json", rows[0])


if __name__ == "__main__":
    import unittest

    unittest.main()
