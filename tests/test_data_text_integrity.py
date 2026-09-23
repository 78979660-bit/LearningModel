from __future__ import annotations

from data_test_support import IsolatedDatabaseTestCase
from study_app.data import database, text_integrity


class DataTextIntegrityTests(IsolatedDatabaseTestCase):
    def test_nested_issue_paths_reasons_and_validation_error(self) -> None:
        value = {
            "replacement": "前缀\ufffd后缀",
            "nested": ["正常文本", {"questions": "内容???损坏"}],
            "private": "文本\ue000字符",
        }

        issues = text_integrity.find_text_integrity_issues(value)

        self.assertEqual(
            [(issue.path, issue.reason) for issue in issues],
            [
                ("$.replacement", "包含 Unicode 替换字符 U+FFFD"),
                ("$.nested[1].questions", "包含连续三个以上问号，疑似字符已被替换"),
                ("$.private", "包含 Unicode 私用区字符，疑似错误解码乱码"),
            ],
        )
        with self.assertRaises(text_integrity.TextIntegrityError) as caught:
            text_integrity.validate_text_integrity(value, context="测试负载")
        self.assertEqual(caught.exception.issues, issues)
        self.assertIn("测试负载 文本完整性检查失败", str(caught.exception))

    def test_mojibake_detection_preview_and_identifier_quoting(self) -> None:
        mojibake = "锛銆绋閿鐢妫杩浣瀛璁棰瑙"
        self.assertTrue(text_integrity.looks_like_mojibake(mojibake))
        self.assertEqual(
            text_integrity.corruption_reason(mojibake),
            "疑似 UTF-8/GBK 错误解码乱码",
        )
        self.assertFalse(text_integrity.looks_like_mojibake("锛銆"))
        self.assertEqual(text_integrity.compact_preview("  一  二  三  "), "'一 二 三'")
        self.assertEqual(text_integrity.quote_identifier('odd"name'), '"odd""name"')

    def test_sqlite_scan_handles_quoted_identifiers_and_max_issue_limit(self) -> None:
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute('CREATE TABLE "odd""table" ("text""value" TEXT, number INTEGER)')
            connection.executemany(
                'INSERT INTO "odd""table"("text""value", number) VALUES (?, ?)',
                [("???", 1), ("第二条???", 2), ("正常", 3)],
            )

        one = text_integrity.scan_sqlite_text_integrity(self.db_path, max_issues=1)
        all_issues = text_integrity.scan_sqlite_text_integrity(self.db_path)

        self.assertEqual(len(one), 1)
        self.assertEqual(len(all_issues), 2)
        self.assertEqual(all_issues[0].path, 'odd"table[rowid=1].text"value')
        self.assertEqual(all_issues[1].path, 'odd"table[rowid=2].text"value')
        self.db_path.unlink()
        self.assertFalse(self.db_path.exists())


if __name__ == "__main__":
    import unittest

    unittest.main()
