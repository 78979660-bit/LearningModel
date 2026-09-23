from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from study_app.core.knowledge_alerts import (
    handle_alert,
    reconcile_knowledge_alerts,
    snooze_alert,
)
from study_app.core.knowledge_states import classify_topic
from study_app.core.topic_identity import topic_key_for_id
from study_app.core.topic_insights import RecentTopicError, TopicInsight
from study_app.data import database
from data_test_support import initialize_legacy_base_database


AS_OF = date(2026, 9, 17)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def failure_insight(topic_id: int, *, as_of: date = AS_OF) -> TopicInsight:
    error = RecentTopicError(
        record_id=topic_id * 100,
        occurred_on=AS_OF - timedelta(days=1),
        title="not persisted by snooze",
        correctness=0.0,
        result_source="problem.correctness",
        error_cause="not persisted by snooze",
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
        as_of_date=as_of,
        model_version="model-v1",
        records_version="records-v1",
    )


class KnowledgeAlertSnoozeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "snooze.sqlite"
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

    def snooze(
        self,
        snoozed_until: date,
        *,
        as_of: date = AS_OF,
        actor: str = "local_user",
    ):
        with database.connect(self.db_path) as connection:
            return snooze_alert(
                self.alert_id,
                snoozed_until,
                as_of,
                connection,
                actor=actor,
            )

    def evidence_counts(self):
        with database.connect_readonly(self.db_path) as connection:
            return tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "learning_records",
                    "problem_attempts",
                    "learning_record_revisions",
                    "alerts",
                    "study_plans",
                )
            )

    def test_a11_active_to_snoozed_updates_only_workflow_and_appends_event(self):
        original = database.list_knowledge_alerts(db_path=self.db_path)[0]
        evidence_before = self.evidence_counts()
        due = AS_OF + timedelta(days=3)
        result = self.snooze(due, actor="desktop_local_user")
        evidence_after = self.evidence_counts()

        self.assertEqual(result.status, "snoozed")
        self.assertEqual(result.snoozed_until, due)
        self.assertEqual(result.recommended_date, original.recommended_date)
        self.assertEqual(result.snapshot, original.snapshot)
        self.assertEqual(result.evidence_version, original.evidence_version)
        self.assertEqual(evidence_after, evidence_before)
        events = database.list_knowledge_alert_events(self.alert_id, self.db_path)
        self.assertEqual([event.event_type for event in events], ["created", "snoozed"])
        event = events[-1]
        self.assertEqual((event.from_status, event.to_status), ("active", "snoozed"))
        self.assertEqual(event.effective_date, AS_OF)
        self.assertEqual(event.actor, "desktop_local_user")
        self.assertEqual(
            event.detail,
            {
                "previous_snoozed_until": None,
                "snoozed_until": due.isoformat(),
            },
        )

    def test_target_date_equal_to_as_of_is_allowed(self):
        result = self.snooze(AS_OF)
        self.assertEqual(result.status, "snoozed")
        self.assertEqual(result.snoozed_until, AS_OF)

    def test_resnooze_appends_history_and_preserves_previous_date(self):
        first_due = AS_OF + timedelta(days=2)
        second_day = AS_OF + timedelta(days=1)
        second_due = AS_OF + timedelta(days=5)
        self.snooze(first_due, actor="first_actor")
        first_event = database.list_knowledge_alert_events(
            self.alert_id, self.db_path
        )[-1]
        result = self.snooze(
            second_due,
            as_of=second_day,
            actor="second_actor",
        )

        self.assertEqual(result.snoozed_until, second_due)
        events = database.list_knowledge_alert_events(self.alert_id, self.db_path)
        self.assertEqual(
            [event.event_type for event in events],
            ["created", "snoozed", "resnoozed"],
        )
        self.assertEqual(events[-2], first_event)
        self.assertEqual((events[-1].from_status, events[-1].to_status), ("snoozed", "snoozed"))
        self.assertEqual(
            events[-1].detail,
            {
                "previous_snoozed_until": first_due.isoformat(),
                "snoozed_until": second_due.isoformat(),
            },
        )
        self.assertEqual(events[-1].actor, "second_actor")

    def test_same_target_resnooze_still_records_the_user_action(self):
        due = AS_OF + timedelta(days=2)
        self.snooze(due)
        self.snooze(due, as_of=AS_OF + timedelta(days=1))
        events = database.list_knowledge_alert_events(self.alert_id, self.db_path)
        self.assertEqual([event.event_type for event in events], ["created", "snoozed", "resnoozed"])
        self.assertEqual(events[-1].detail["previous_snoozed_until"], due.isoformat())

    def test_expired_snooze_must_reconcile_before_another_snooze(self):
        due = AS_OF + timedelta(days=1)
        self.snooze(due)
        before = file_hash(self.db_path)
        with self.assertRaisesRegex(ValueError, "必须先按当前条件执行协调重算"):
            self.snooze(
                AS_OF + timedelta(days=4),
                as_of=due,
            )
        self.assertEqual(file_hash(self.db_path), before)

    def test_before_due_reconcile_keeps_snoozed_then_due_reactivates(self):
        due = AS_OF + timedelta(days=2)
        self.snooze(due)
        before_due = AS_OF + timedelta(days=1)
        before_state = classify_topic(
            replace(self.insight, as_of_date=before_due), {}
        )
        with database.connect(self.db_path) as connection:
            before = reconcile_knowledge_alerts(
                (before_state,), before_due, connection
            )
        self.assertEqual(before.unchanged_ids, (self.alert_id,))
        self.assertEqual(
            database.list_knowledge_alerts(db_path=self.db_path)[0].status,
            "snoozed",
        )

        due_state = classify_topic(replace(self.insight, as_of_date=due), {})
        with database.connect(self.db_path) as connection:
            at_due = reconcile_knowledge_alerts((due_state,), due, connection)
        self.assertEqual(at_due.reactivated_ids, (self.alert_id,))
        self.assertEqual(
            database.list_knowledge_alerts(db_path=self.db_path)[0].status,
            "active",
        )

    def test_due_reconcile_resolves_when_condition_disappears(self):
        due = AS_OF + timedelta(days=2)
        self.snooze(due)
        with database.connect(self.db_path) as connection:
            result = reconcile_knowledge_alerts((), due, connection)
        self.assertEqual(result.resolved_ids, (self.alert_id,))
        alert = database.list_knowledge_alerts(db_path=self.db_path)[0]
        self.assertEqual(alert.status, "resolved")

    def test_invalid_ids_dates_actor_and_backdated_event_are_atomic(self):
        invalid_calls = (
            (True, AS_OF, AS_OF, "local_user"),
            (0, AS_OF, AS_OF, "local_user"),
            (self.alert_id, "2026-09-20", AS_OF, "local_user"),
            (self.alert_id, datetime(2026, 9, 20, 8), AS_OF, "local_user"),
            (self.alert_id, AS_OF - timedelta(days=1), AS_OF, "local_user"),
            (self.alert_id, AS_OF, AS_OF - timedelta(days=1), "local_user"),
            (self.alert_id, AS_OF, AS_OF, ""),
            (self.alert_id, AS_OF, AS_OF, "display name"),
        )
        for alert_id, due, as_of, actor in invalid_calls:
            with self.subTest(alert_id=alert_id, due=due, as_of=as_of, actor=actor):
                before = file_hash(self.db_path)
                with database.connect(self.db_path) as connection:
                    with self.assertRaises(ValueError):
                        snooze_alert(
                            alert_id,
                            due,
                            as_of,
                            connection,
                            actor=actor,
                        )
                self.assertEqual(file_hash(self.db_path), before)

    def test_handled_instance_is_rejected_without_writes(self):
        with database.connect(self.db_path) as connection:
            handle_alert(self.alert_id, AS_OF, connection)
        before = file_hash(self.db_path)
        with self.assertRaisesRegex(ValueError, "只有 active 或 snoozed"):
            self.snooze(AS_OF + timedelta(days=2))
        self.assertEqual(file_hash(self.db_path), before)

    def test_resolved_instance_is_rejected_without_writes(self):
        with database.connect(self.db_path) as connection:
            reconcile_knowledge_alerts((), AS_OF, connection)
        before = file_hash(self.db_path)
        with self.assertRaisesRegex(ValueError, "只有 active 或 snoozed"):
            self.snooze(AS_OF + timedelta(days=2))
        self.assertEqual(file_hash(self.db_path), before)

    def test_unknown_instance_is_rejected_without_writes(self):
        before = file_hash(self.db_path)
        with database.connect(self.db_path) as connection:
            with self.assertRaisesRegex(LookupError, "未找到知识预警实例"):
                snooze_alert(
                    99999,
                    AS_OF + timedelta(days=1),
                    AS_OF,
                    connection,
                )
        self.assertEqual(file_hash(self.db_path), before)

    def test_event_insert_failure_rolls_back_status_and_target_date(self):
        with database.connect(self.db_path) as connection:
            connection.executescript(
                """
                CREATE TRIGGER fail_snooze_event
                BEFORE INSERT ON knowledge_alert_events
                WHEN NEW.event_type IN ('snoozed', 'resnoozed')
                BEGIN
                    SELECT RAISE(ABORT, 'forced snooze event failure');
                END;
                """
            )
        with database.connect(self.db_path) as connection:
            with self.assertRaisesRegex(Exception, "forced snooze event failure"):
                snooze_alert(
                    self.alert_id,
                    AS_OF + timedelta(days=2),
                    AS_OF,
                    connection,
                )
        alert = database.list_knowledge_alerts(db_path=self.db_path)[0]
        events = database.list_knowledge_alert_events(self.alert_id, self.db_path)
        self.assertEqual(alert.status, "active")
        self.assertIsNone(alert.snoozed_until)
        self.assertEqual([event.event_type for event in events], ["created"])

    def test_missing_f2_tables_are_not_installed_by_snooze(self):
        bare_path = Path(self.temp_dir.name) / "bare.sqlite"
        initialize_legacy_base_database(bare_path)
        before = file_hash(bare_path)
        with database.connect(bare_path) as connection:
            with self.assertRaises(database.DatabaseNotInitializedError):
                snooze_alert(
                    1,
                    AS_OF + timedelta(days=1),
                    AS_OF,
                    connection,
                )
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
