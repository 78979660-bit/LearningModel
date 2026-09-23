# Integration test: REAL projection module + REAL DashboardState-today flow (no stubs).
# Re-review §六.1/§六.6: real UI chain loads >=1 node, no error label; as_of_date unification.
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from study_app.core.chapter_graph_projection import (  # noqa: E402
    _coerce_as_of_date,
    build_chapter_graph_snapshot,
    snapshot_cache_key,
)
from study_app.data import database  # noqa: E402
from study_app.data.subject_repository import (  # noqa: E402
    SubjectCatalogRepository,
    install_subject_lifecycle_schema,
)

STRUCT = "structure:v1:" + "b" * 64


def _seed_full(tmp: Path, with_topics: bool):
    db = tmp / "l.sqlite"
    model = tmp / "m.json"
    model.write_text(json.dumps({"model_name": "m", "subjects": []}, ensure_ascii=False), encoding="utf-8")
    database.initialize_database(db)
    install_subject_lifecycle_schema(db)
    with database.connect(db) as c:
        for st in (
            database.KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL,
            database.KNOWLEDGE_PREREQUISITES_TABLE_SQL,
            database.KNOWLEDGE_PREREQUISITES_REVERSE_INDEX_SQL,
            database.KNOWLEDGE_ALERTS_TABLE_SQL,
            database.KNOWLEDGE_ALERT_EVENTS_TABLE_SQL,
            database.KNOWLEDGE_ALERT_INDEXES_SQL,
        ):
            for part in st.split(";"):
                if part.strip():
                    c.execute(part)
        c.commit()
    repo = SubjectCatalogRepository(db)
    subject = repo.create_subject("测试学科")
    ident = repo.create_module(subject.subject_key, "命题逻辑")
    with database.connect(db) as c:
        c.execute("PRAGMA foreign_keys=OFF")
        legacy_sid = c.execute(
            "INSERT INTO subjects(name, status) VALUES (?, 'active')", ("测试学科",)
        ).lastrowid
        legacy_mid = c.execute(
            "INSERT INTO modules(subject_id, name) VALUES (?, ?)", (legacy_sid, "命题逻辑")
        ).lastrowid
        tk = None
        if with_topics:
            tid = c.execute(
                "INSERT INTO topics(module_id, name, status, mastery, importance, difficulty, source_json) VALUES (?,?,?,?,?,?,?)",
                (legacy_mid, "命题", "learning", 0, 0, 0, "{}"),
            ).lastrowid
            tk = "topic:v1:" + "0" * 64
            c.execute(
                "INSERT INTO knowledge_topic_registry(topic_key, topic_id, identity_version) VALUES (?,?,?)",
                (tk, tid, "topic-identity-v1"),
            )
        c.execute(
            "INSERT INTO subject_structure_versions(subject_key, structure_version, is_current, source_reference) VALUES (?,?,1,'int-fixture')",
            (subject.subject_key, STRUCT),
        )
        c.execute(
            "INSERT INTO subject_structure_modules(structure_version, module_key, module_order) VALUES (?,?,?)",
            (STRUCT, ident.module_key, 0),
        )
        if with_topics:
            c.execute(
                "INSERT INTO subject_structure_topics(structure_version, module_key, topic_key, topic_order, importance_bp) VALUES (?,?,?,?,?)",
                (STRUCT, ident.module_key, tk, 0, 5000),
            )
        c.commit()
    return db, subject.subject_key


class AsOfDateUnificationTests(unittest.TestCase):
    def test_coerce_accepts_date_and_string(self):
        self.assertEqual(_coerce_as_of_date(date(2026, 9, 20)), "2026-09-20")
        self.assertEqual(_coerce_as_of_date("2026-09-20"), "2026-09-20")
        with self.assertRaises(TypeError):
            _coerce_as_of_date(123)

    def test_cache_key_accepts_date_object(self):
        tmp = Path(tempfile.mkdtemp(prefix="cg_int_key_"))
        db = tmp / "l.sqlite"
        database.initialize_database(db)
        key = snapshot_cache_key("subj:x", db_path=db, as_of_date=date(2026, 9, 20))
        self.assertIsInstance(key, tuple)


class RealUiChainTests(unittest.TestCase):
    def test_real_panel_loads_nodes_with_real_dashboard_today(self):
        """§六.6: real UI + real projection + real DashboardState.today → nodes>0, no error."""
        tmp = Path(tempfile.mkdtemp(prefix="cg_int_ui_"))
        db, subject_key = _seed_full(tmp, with_topics=True)

        from PySide6.QtCore import QElapsedTimer
        from PySide6.QtWidgets import QApplication, QLabel

        app = QApplication.instance() or QApplication([])

        from study_app.core.dashboard import DashboardState
        from study_app.ui.chapter_graph_view import chapter_graph_panel

        dash = DashboardState(date(2026, 9, 20), date(2026, 9, 20), 60.0, (), (), (), (), ())
        panel = chapter_graph_panel(parent_callable=lambda: dash, db_path=db)
        panel.resize(1080, 1400)
        panel.show()
        timer = QElapsedTimer()
        timer.start()
        while timer.elapsed() < 2500:
            app.processEvents()
            app.thread().msleep(20)
        canvas = panel._graph_canvas
        self.assertEqual(len(canvas.module_keys()), 1)
        error_label = panel.findChild(QLabel, "GraphErrorLabel")
        if error_label is not None:
            self.assertNotIn("加载失败", error_label.text())
        panel.close()
        app.processEvents()

    def test_real_snapshot_matches_topic_binding(self):
        tmp = Path(tempfile.mkdtemp(prefix="cg_int_snap_"))
        db, subject_key = _seed_full(tmp, with_topics=True)
        snap = build_chapter_graph_snapshot(subject_key, db_path=db, as_of_date="2026-09-20")
        self.assertEqual(len(snap.nodes), 1)
        node = snap.nodes[0]
        self.assertEqual(node.topic_count, 1)
        self.assertEqual(node.learning_state, "unlearned")
        self.assertIsNone(node.mastery_point)


if __name__ == "__main__":
    unittest.main()
