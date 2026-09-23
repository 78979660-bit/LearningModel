from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from study_app.core.knowledge_alerts import handle_alert, reconcile_knowledge_alerts
from study_app.core.knowledge_states import classify_topic
from study_app.core.topic_identity import topic_key_for_id
from study_app.core.topic_insights import RecentTopicError, TopicInsight
from study_app.data import database
from data_test_support import initialize_legacy_base_database


AS_OF = date(2026, 9, 17)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def failure_insight(topic_id: int) -> TopicInsight:
    error = RecentTopicError(
        record_id=topic_id * 100,
        occurred_on=AS_OF - timedelta(days=1),
        title="not persisted by handle",
        correctness=0.0,
        result_source="problem.correctness",
        error_cause="not persisted by handle",
    )
    return TopicInsight(
        topic_key=topic_key_for_id(topic_id),
        topic_id=topic_id,
        subject_name="S",
        module_name="M",
        topic_name="T",
        subject_status="active",
        course_status="learning",
        is_archived=False,
        observation_count=4,
        exercise_count=2,
        evidence_confidence=0.60,
        evidence_version="evidence-v1",
        mastery_point=0.50,
        mastery_source="bkt_observed",
        mastery_interval=(0.30, 0.70),
        mastery_interval_rule_version="mastery-interval-v1",
        recent_error=error,
        last_observation_date=error.occurred_on,
        last_observation_correctness=0.0,
        last_review_date=AS_OF - timedelta(days=2),
        review_count=2,
        half_life_days=5.0,
        recall_probability=0.70,
        target_recall=0.78,
        recommended_date=AS_OF,
        recommendation_source="half_life_target_recall",
        direct_prerequisite_keys=(),
        unresolved_prerequisite_keys=(),
        as_of_date=AS_OF,
        model_version="model-v1",
        records_version="records-v1",
    )


class KnowledgeAlertHandleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "handle.sqlite"
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute(database.KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL)
            subject_id = connection.execute(
                "INSERT INTO subjects(name, source_json) VALUES ('S', '{}')"
            ).lastrowid
            module_id = connection.execute(
                """
                INSERT INTO modules(subject_id, name, source_json)
                VALUES (?, 'M', '{}')
                """,
                (subject_id,),
            ).lastrowid
            self.topic_id = int(
                connection.execute(
                    """
                    INSERT INTO topics(module_id, name, source_json)
                    VALUES (?, 'T', '{}')
                    """,
                    (module_id,),
                ).lastrowid
            )
        database.register_topic_identities(self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute(database.KNOWLEDGE_ALERTS_TABLE_SQL)
            connection.execute(database.KNOWLEDGE_ALERT_EVENTS_TABLE_SQL)
            connection.executescript(database.KNOWLEDGE_ALERT_INDEXES_SQL)
        self.insight = failure_insight(self.topic_id)
        self.state = classify_topic(self.insight, {})
        with database.connect(self.db_path) as connection:
            self.alert_id = reconcile_knowledge_alerts(
                (self.state,), AS_OF, connection
            ).created_ids[0]

    def handle(self, *, actor: str = "local_user", effective_date=AS_OF):
        with database.connect(self.db_path) as connection:
            return handle_alert(
                self.alert_id,
                effective_date,
                connection,
                actor=actor,
            )

    def rows(self):
        with database.connect_readonly(self.db_path) as connection:
            alert = tuple(connection.execute("SELECT * FROM knowledge_alerts"))
            events = tuple(
                connection.execute("SELECT * FROM knowledge_alert_events ORDER BY id")
            )
            evidence_counts = tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "learning_records",
                    "problem_attempts",
                    "learning_record_revisions",
                    "alerts",
                )
            )
        return alert, events, evidence_counts

    def test_a10_active_handle_updates_instance_and_appends_one_event(self):
        before_alerts, before_events, evidence_before = self.rows()
        result = self.handle(actor="desktop_local_user")
        after_alerts, after_events, evidence_after = self.rows()

        self.assertEqual(result.id, self.alert_id)
        self.assertEqual(result.status, "handled")
        self.assertEqual(result.handled_at, AS_OF.isoformat())
        self.assertEqual(result.snapshot, database.list_knowledge_alerts(db_path=self.db_path)[0].snapshot)
        self.assertEqual(len(after_alerts), len(before_alerts))
        self.assertEqual(len(after_events), len(before_events) + 1)
        event = database.list_knowledge_alert_events(self.alert_id, self.db_path)[-1]
        self.assertEqual(event.event_type, "handled")
        self.assertEqual((event.from_status, event.to_status), ("active", "handled"))
        self.assertEqual(event.effective_date, AS_OF)
        self.assertEqual(event.actor, "desktop_local_user")
        self.assertEqual(event.detail, {})
        self.assertEqual(evidence_after, evidence_before)

    def test_handle_does_not_change_learning_evidence_or_model_inputs(self):
        before = {
            "observation_count": self.insight.observation_count,
            "recent_error": self.insight.recent_error,
            "mastery_point": self.insight.mastery_point,
            "model_version": self.insight.model_version,
            "records_version": self.insight.records_version,
        }
        self.handle()
        after = {
            "observation_count": self.insight.observation_count,
            "recent_error": self.insight.recent_error,
            "mastery_point": self.insight.mastery_point,
            "model_version": self.insight.model_version,
            "records_version": self.insight.records_version,
        }
        self.assertEqual(after, before)
        with database.connect(self.db_path) as connection:
            reconcile_knowledge_alerts((self.state,), AS_OF, connection)
        alert = database.list_knowledge_alerts(db_path=self.db_path)[0]
        self.assertEqual(alert.status, "handled")
        self.assertEqual(len(database.list_knowledge_alert_events(self.alert_id, self.db_path)), 2)

    def test_repeated_handle_is_byte_stable_and_preserves_first_actor_and_date(self):
        first = self.handle(actor="first_actor")
        before = file_hash(self.db_path)
        second = self.handle(
            actor="second_actor",
            effective_date=AS_OF + timedelta(days=1),
        )
        self.assertEqual(second, first)
        self.assertEqual(file_hash(self.db_path), before)
        events = database.list_knowledge_alert_events(self.alert_id, self.db_path)
        self.assertEqual([event.event_type for event in events], ["created", "handled"])
        self.assertEqual(events[-1].actor, "first_actor")

    def test_snoozed_instance_is_rejected_without_writes(self):
        with database.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE knowledge_alerts
                SET status = 'snoozed', snoozed_until = '2026-09-20'
                WHERE id = ?
                """,
                (self.alert_id,),
            )
        before = file_hash(self.db_path)
        with self.assertRaisesRegex(ValueError, "只有 active"):
            self.handle()
        self.assertEqual(file_hash(self.db_path), before)

    def test_resolved_instance_is_rejected_without_writes(self):
        with database.connect(self.db_path) as connection:
            reconcile_knowledge_alerts((), AS_OF, connection)
        before = file_hash(self.db_path)
        with self.assertRaisesRegex(ValueError, "只有 active"):
            self.handle()
        self.assertEqual(file_hash(self.db_path), before)

    def test_unknown_instance_is_rejected_without_writes(self):
        before = file_hash(self.db_path)
        with database.connect(self.db_path) as connection:
            with self.assertRaisesRegex(LookupError, "未找到知识预警实例"):
                handle_alert(99999, AS_OF, connection)
        self.assertEqual(file_hash(self.db_path), before)

    def test_invalid_inputs_are_rejected_before_writes(self):
        invalid_calls = (
            (True, AS_OF, "local_user"),
            (0, AS_OF, "local_user"),
            (self.alert_id, "2026-09-17", "local_user"),
            (self.alert_id, datetime(2026, 9, 17, 8), "local_user"),
            (self.alert_id, AS_OF - timedelta(days=1), "local_user"),
            (self.alert_id, AS_OF, ""),
            (self.alert_id, AS_OF, "user display name"),
            (self.alert_id, AS_OF, "x" * 65),
        )
        for alert_id, effective_date, actor in invalid_calls:
            with self.subTest(alert_id=alert_id, effective_date=effective_date, actor=actor):
                before = file_hash(self.db_path)
                with database.connect(self.db_path) as connection:
                    with self.assertRaises(ValueError):
                        handle_alert(
                            alert_id,
                            effective_date,
                            connection,
                            actor=actor,
                        )
                self.assertEqual(file_hash(self.db_path), before)

    def test_event_insert_failure_rolls_back_instance_update(self):
        with database.connect(self.db_path) as connection:
            connection.executescript(
                """
                CREATE TRIGGER fail_handled_event
                BEFORE INSERT ON knowledge_alert_events
                WHEN NEW.event_type = 'handled'
                BEGIN
                    SELECT RAISE(ABORT, 'forced handled event failure');
                END;
                """
            )
        with database.connect(self.db_path) as connection:
            with self.assertRaisesRegex(Exception, "forced handled event failure"):
                handle_alert(self.alert_id, AS_OF, connection)
        alert = database.list_knowledge_alerts(db_path=self.db_path)[0]
        events = database.list_knowledge_alert_events(self.alert_id, self.db_path)
        self.assertEqual(alert.status, "active")
        self.assertIsNone(alert.handled_at)
        self.assertEqual([event.event_type for event in events], ["created"])

    def test_missing_f2_tables_are_not_installed_by_handle(self):
        bare_path = Path(self.temp_dir.name) / "bare.sqlite"
        initialize_legacy_base_database(bare_path)
        before = file_hash(bare_path)
        with database.connect(bare_path) as connection:
            with self.assertRaises(database.DatabaseNotInitializedError):
                handle_alert(1, AS_OF, connection)
        self.assertEqual(file_hash(bare_path), before)
        with database.connect_readonly(bare_path) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertNotIn("knowledge_alerts", names)
        self.assertNotIn("knowledge_alert_events", names)


if __name__ == "__main__":
    unittest.main()
