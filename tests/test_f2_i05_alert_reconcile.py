from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from study_app.core.knowledge_alerts import reconcile_knowledge_alerts
from study_app.core.knowledge_states import classify_topic
from study_app.core.topic_identity import topic_key_for_id
from study_app.core.topic_insights import RecentTopicError, TopicInsight
from study_app.data import database
from data_test_support import initialize_legacy_base_database


AS_OF = date(2026, 9, 17)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def insight(topic_id: int, *, as_of: date = AS_OF, **changes) -> TopicInsight:
    base = TopicInsight(
        topic_key=topic_key_for_id(topic_id),
        topic_id=topic_id,
        subject_name="S",
        module_name="M",
        topic_name=f"T{topic_id}",
        subject_status="active",
        course_status="learning",
        is_archived=False,
        observation_count=4,
        exercise_count=2,
        evidence_confidence=0.60,
        evidence_version="evidence-v1",
        mastery_point=0.75,
        mastery_source="bkt_observed",
        mastery_interval=(0.55, 0.95),
        mastery_interval_rule_version="mastery-interval-v1",
        recent_error=None,
        last_observation_date=as_of - timedelta(days=10),
        last_observation_correctness=1.0,
        last_review_date=as_of - timedelta(days=2),
        review_count=2,
        half_life_days=8.0,
        recall_probability=0.90,
        target_recall=0.78,
        recommended_date=as_of + timedelta(days=3),
        recommendation_source="half_life_target_recall",
        direct_prerequisite_keys=(),
        unresolved_prerequisite_keys=(),
        as_of_date=as_of,
        model_version="model-v1",
        records_version="records-v1",
    )
    return replace(base, **changes)


def recent_failure(topic_id: int, *, as_of: date = AS_OF, **changes):
    error = RecentTopicError(
        record_id=topic_id * 100,
        occurred_on=as_of - timedelta(days=1),
        title="sensitive statement is deliberately not persisted",
        correctness=0.0,
        result_source="problem.correctness",
        error_cause="sensitive cause is deliberately not persisted",
    )
    return insight(
        topic_id,
        as_of=as_of,
        recent_error=error,
        last_observation_date=error.occurred_on,
        last_observation_correctness=0.0,
        **changes,
    )


class KnowledgeAlertReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "alerts.sqlite"
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
            self.topic_ids = tuple(
                int(
                    connection.execute(
                        """
                        INSERT INTO topics(module_id, name, source_json)
                        VALUES (?, ?, '{}')
                        """,
                        (module_id, f"T{index}"),
                    ).lastrowid
                )
                for index in range(1, 8)
            )
        database.register_topic_identities(self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute(database.KNOWLEDGE_ALERTS_TABLE_SQL)
            connection.execute(database.KNOWLEDGE_ALERT_EVENTS_TABLE_SQL)
            connection.executescript(database.KNOWLEDGE_ALERT_INDEXES_SQL)

    def state(self, value: TopicInsight):
        return classify_topic(value, {})

    def reconcile(self, values, *, as_of: date = AS_OF):
        with database.connect(self.db_path) as connection:
            return reconcile_knowledge_alerts(values, as_of, connection)

    def raw_counts(self):
        with database.connect_readonly(self.db_path) as connection:
            return tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("knowledge_alerts", "knowledge_alert_events", "alerts")
            )

    def test_only_default_action_states_are_persisted_and_generic_alerts_untouched(self):
        failure = self.state(recent_failure(self.topic_ids[0]))
        insufficient = self.state(
            insight(self.topic_ids[1], evidence_confidence=0.20)
        )
        overdue = self.state(
            insight(
                self.topic_ids[2],
                recall_probability=0.50,
                recommended_date=AS_OF - timedelta(days=2),
            )
        )
        stable = self.state(insight(self.topic_ids[3]))
        unlearned = self.state(
            insight(
                self.topic_ids[4],
                course_status="not_started",
                observation_count=0,
                exercise_count=0,
                evidence_confidence=0.0,
                mastery_source="baseline_prior",
                mastery_interval=None,
                last_observation_date=None,
                last_observation_correctness=None,
                last_review_date=None,
                review_count=0,
                recommended_date=None,
                recommendation_source="unavailable",
            )
        )
        archived = self.state(
            replace(
                recent_failure(self.topic_ids[5]),
                subject_status="archived",
                is_archived=True,
            )
        )

        result = self.reconcile(
            (stable, archived, failure, unlearned, overdue, insufficient)
        )
        self.assertEqual(len(result.created_ids), 3)
        alerts = database.list_knowledge_alerts(db_path=self.db_path)
        self.assertEqual(
            {item.alert_type for item in alerts},
            {"recent_failure", "insufficient_evidence", "overdue_review"},
        )
        self.assertEqual({item.status for item in alerts}, {"active"})
        self.assertEqual(self.raw_counts(), (3, 3, 0))
        payload_text = repr(tuple(item.snapshot for item in alerts))
        self.assertNotIn("sensitive statement", payload_text)
        self.assertNotIn("sensitive cause", payload_text)

    def test_same_snapshot_is_byte_stable_and_adds_no_event(self):
        state = self.state(recent_failure(self.topic_ids[0]))
        first = self.reconcile((state,))
        before = file_hash(self.db_path)
        second = self.reconcile((state,))
        after = file_hash(self.db_path)
        self.assertEqual(len(first.created_ids), 1)
        self.assertEqual(second.created_ids, ())
        self.assertEqual(second.unchanged_ids, first.created_ids)
        self.assertEqual(before, after)
        self.assertEqual(self.raw_counts(), (1, 1, 0))

    def test_condition_disappearance_resolves_once(self):
        state = self.state(recent_failure(self.topic_ids[0]))
        alert_id = self.reconcile((state,)).created_ids[0]
        result = self.reconcile(())
        self.assertEqual(result.resolved_ids, (alert_id,))
        self.assertEqual(
            database.list_knowledge_alerts(db_path=self.db_path)[0].status,
            "resolved",
        )
        events = database.list_knowledge_alert_events(alert_id, self.db_path)
        self.assertEqual([event.event_type for event in events], ["created", "condition_cleared"])
        before = file_hash(self.db_path)
        repeated = self.reconcile(())
        self.assertEqual(repeated.resolved_ids, ())
        self.assertEqual(file_hash(self.db_path), before)

    def test_a5_correction_resolves_failure_and_creates_current_condition(self):
        topic_id = self.topic_ids[0]
        failure = recent_failure(topic_id, evidence_confidence=0.30)
        old_id = self.reconcile((self.state(failure),)).created_ids[0]
        corrected = replace(
            failure,
            evidence_version="evidence-v2",
            last_observation_date=AS_OF,
            last_observation_correctness=0.90,
        )
        result = self.reconcile((self.state(corrected),))
        self.assertEqual(result.resolved_ids, (old_id,))
        self.assertEqual(len(result.created_ids), 1)
        alerts = database.list_knowledge_alerts(db_path=self.db_path)
        self.assertEqual(
            [(item.alert_type, item.status) for item in alerts],
            [("recent_failure", "resolved"), ("insufficient_evidence", "active")],
        )

    def test_evidence_revision_supersedes_instance_and_replay_is_idempotent(self):
        topic_id = self.topic_ids[1]
        first_state = self.state(insight(topic_id, evidence_confidence=0.20))
        old_id = self.reconcile((first_state,)).created_ids[0]
        revised_state = self.state(
            insight(
                topic_id,
                evidence_confidence=0.20,
                evidence_version="evidence-v2",
                records_version="revision-2",
            )
        )
        revised = self.reconcile((revised_state,))
        self.assertEqual(revised.resolved_ids, (old_id,))
        self.assertEqual(len(revised.created_ids), 1)
        before = file_hash(self.db_path)
        replay = self.reconcile((revised_state,))
        self.assertEqual(replay.created_ids, ())
        self.assertEqual(file_hash(self.db_path), before)

    def test_a7_future_only_records_version_does_not_change_fingerprint(self):
        topic_id = self.topic_ids[1]
        before_state = self.state(
            insight(topic_id, evidence_confidence=0.20, records_version="before")
        )
        alert_id = self.reconcile((before_state,)).created_ids[0]
        after_state = self.state(
            insight(topic_id, evidence_confidence=0.20, records_version="future-only")
        )
        result = self.reconcile((after_state,))
        self.assertEqual(result.unchanged_ids, (alert_id,))
        self.assertEqual(self.raw_counts(), (1, 1, 0))

    def test_handled_same_fingerprint_is_not_revived(self):
        state = self.state(recent_failure(self.topic_ids[0]))
        alert_id = self.reconcile((state,)).created_ids[0]
        with database.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE knowledge_alerts
                SET status = 'handled', handled_at = '2026-09-17T08:00:00'
                WHERE id = ?
                """,
                (alert_id,),
            )
        before = file_hash(self.db_path)
        result = self.reconcile((state,))
        self.assertEqual(result.unchanged_ids, (alert_id,))
        self.assertEqual(file_hash(self.db_path), before)
        self.assertEqual(
            database.list_knowledge_alerts(db_path=self.db_path)[0].status,
            "handled",
        )

    def test_snoozed_before_due_is_hidden_and_byte_stable(self):
        state = self.state(recent_failure(self.topic_ids[0]))
        alert_id = self.reconcile((state,)).created_ids[0]
        with database.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE knowledge_alerts SET status = 'snoozed', snoozed_until = ? WHERE id = ?",
                ((AS_OF + timedelta(days=2)).isoformat(), alert_id),
            )
        before = file_hash(self.db_path)
        result = self.reconcile((state,))
        self.assertEqual(result.unchanged_ids, (alert_id,))
        self.assertEqual(file_hash(self.db_path), before)

    def test_snooze_due_reactivates_once_when_condition_remains(self):
        topic_id = self.topic_ids[0]
        state = self.state(recent_failure(topic_id))
        alert_id = self.reconcile((state,)).created_ids[0]
        due = AS_OF + timedelta(days=1)
        with database.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE knowledge_alerts SET status = 'snoozed', snoozed_until = ? WHERE id = ?",
                (due.isoformat(), alert_id),
            )
        due_state = self.state(replace(state.insight, as_of_date=due))
        result = self.reconcile((due_state,), as_of=due)
        self.assertEqual(result.reactivated_ids, (alert_id,))
        alert = database.list_knowledge_alerts(db_path=self.db_path)[0]
        self.assertEqual(alert.status, "active")
        self.assertIsNone(alert.snoozed_until)
        before = file_hash(self.db_path)
        repeated = self.reconcile((due_state,), as_of=due)
        self.assertEqual(repeated.reactivated_ids, ())
        self.assertEqual(file_hash(self.db_path), before)

    def test_snooze_due_resolves_when_condition_is_gone(self):
        state = self.state(recent_failure(self.topic_ids[0]))
        alert_id = self.reconcile((state,)).created_ids[0]
        with database.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE knowledge_alerts SET status = 'snoozed', snoozed_until = ? WHERE id = ?",
                (AS_OF.isoformat(), alert_id),
            )
        result = self.reconcile(())
        self.assertEqual(result.resolved_ids, (alert_id,))
        event = database.list_knowledge_alert_events(alert_id, self.db_path)[-1]
        self.assertEqual((event.from_status, event.to_status), ("snoozed", "resolved"))

    def test_a9_archiving_resolves_existing_alert_and_never_creates_one(self):
        topic_id = self.topic_ids[0]
        active_insight = recent_failure(topic_id)
        alert_id = self.reconcile((self.state(active_insight),)).created_ids[0]
        archived = self.state(
            replace(active_insight, subject_status="archived", is_archived=True)
        )
        result = self.reconcile((archived,))
        self.assertEqual(result.resolved_ids, (alert_id,))
        self.assertEqual(len(database.list_knowledge_alerts(db_path=self.db_path)), 1)

    def test_unknown_topic_makes_mixed_batch_atomic(self):
        known = self.state(recent_failure(self.topic_ids[0]))
        unknown = self.state(recent_failure(999))
        before = file_hash(self.db_path)
        with self.assertRaisesRegex(LookupError, "未找到知识点身份"):
            self.reconcile((known, unknown))
        self.assertEqual(file_hash(self.db_path), before)
        self.assertEqual(self.raw_counts(), (0, 0, 0))

    def test_read_paths_and_general_schema_do_not_install_f2_tables(self):
        bare_path = Path(self.temp_dir.name) / "bare.sqlite"
        initialize_legacy_base_database(bare_path)
        before = file_hash(bare_path)
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.list_knowledge_alerts(db_path=bare_path)
        with database.connect(bare_path) as connection:
            with self.assertRaises(database.DatabaseNotInitializedError):
                reconcile_knowledge_alerts((), AS_OF, connection)
        self.assertEqual(file_hash(bare_path), before)
        with database.connect_readonly(bare_path) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        self.assertNotIn("knowledge_alerts", names)
        self.assertNotIn("knowledge_alert_events", names)


if __name__ == "__main__":
    unittest.main()
