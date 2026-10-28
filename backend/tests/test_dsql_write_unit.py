"""Stable-ID reconciliation and transaction-boundary regression tests."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from lambdaops_m3.contracts import EventContractError, parse_eventbridge_alarm
from lambdaops_m3.dsql_projection import plan_projection
from lambdaops_m3.dsql_store import (
    DsqlConcurrencyError, DsqlIncidentStore, DsqlProjectionTooLarge,
    _retryable, _validate_input,
)
from lambdaops_m3.store import ApplyResult, TransitionConflict, alarm_key


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def fixture(name="alarm.json"):
    return json.loads((FIXTURES / name).read_text())


def transition(event):
    return parse_eventbridge_alarm(event, {event["resources"][0]}, "slash-api", "dev")


def sequence(states):
    events = []
    for index, state in enumerate(states):
        event = fixture()
        event["id"] = "event-%d" % index
        event["detail"]["state"] = {"value": state, "timestamp": (NOW + timedelta(minutes=index)).isoformat()}
        event["detail"]["previousState"] = {
            "value": states[index - 1] if index else "OK",
            "timestamp": (NOW + timedelta(minutes=index - 1)).isoformat(),
        }
        events.append(transition(event))
    return events


class MemoryProjection:
    """Persist the pure plan, including retained redirect rows, for replay tests."""

    def __init__(self):
        self.events, self.rows = [], {}
        self.counter = 0

    def new_id(self):
        self.counter += 1
        return uuid.UUID(int=self.counter)

    def apply(self, event):
        before = {key: row["created_at"] for key, row in self.rows.items()}
        self.events.append(event)
        plan = plan_projection(
            self.events, list(self.rows.values()), alarm_key(event),
            NOW + timedelta(seconds=len(self.events)), self.new_id,
        )
        for incident_id, target in plan.redirects.items():
            self.rows[incident_id]["redirect_to_incident_id"] = target
        self.rows.update(deepcopy(plan.canonical))
        for key, created in before.items():
            if self.rows[key]["created_at"] != created:
                raise AssertionError("published creation time changed")
        return plan


class StableIdentityTests(unittest.TestCase):
    def test_late_earlier_alarm_keeps_original_id_and_creation_time(self):
        events = sequence(["ALARM", "INSUFFICIENT_DATA", "ALARM"])
        store = MemoryProjection()
        store.apply(events[2])
        original_id = next(iter(store.rows))
        created = store.rows[original_id]["created_at"]
        store.apply(events[1])
        plan = store.apply(events[0])
        self.assertEqual(list(plan.canonical), [original_id])
        row = plan.canonical[original_id]
        self.assertEqual(row["anchor_event_key"], events[0].dedupe_key)
        self.assertEqual(row["detected_at"], events[0].transitioned_at)
        self.assertEqual(row["created_at"], created)

    def test_bridge_keeps_first_created_incident_even_if_its_anchor_was_later(self):
        events = sequence(["ALARM", "INSUFFICIENT_DATA", "ALARM"])
        store = MemoryProjection()
        store.apply(events[2])
        survivor = next(iter(store.rows))
        store.apply(events[0])
        loser = next(key for key in store.rows if key != survivor)
        plan = store.apply(events[1])
        self.assertEqual(list(plan.canonical), [survivor])
        self.assertEqual(plan.redirects, {loser: survivor})
        self.assertEqual(plan.canonical[survivor]["anchor_event_key"], events[0].dedupe_key)

    def test_creation_time_tie_breaks_by_id(self):
        events = sequence(["ALARM", "INSUFFICIENT_DATA", "ALARM"])
        store = MemoryProjection()
        store.apply(events[0])
        store.apply(events[2])
        for row in store.rows.values():
            row["created_at"] = NOW
        expected = min(store.rows)
        plan = store.apply(events[1])
        self.assertEqual(list(plan.canonical), [expected])

    def test_all_5040_delivery_orders_preserve_ids_and_flatten_redirects(self):
        events = sequence(["ALARM", "INSUFFICIENT_DATA", "ALARM", "INSUFFICIENT_DATA", "ALARM", "OK", "ALARM"])
        for order in itertools.permutations(events):
            store = MemoryProjection()
            published = set()
            for event in order:
                plan = store.apply(event)
                self.assertTrue(published <= store.rows.keys())
                published = set(store.rows)
                for incident_id, target in plan.redirects.items():
                    self.assertNotEqual(incident_id, target)
                    self.assertIn(target, plan.canonical)
                    self.assertIsNone(store.rows[target]["redirect_to_incident_id"])
            self.assertEqual(len(plan.canonical), 2)
            self.assertEqual(sorted(row["alarm_state"] for row in plan.canonical.values()), ["ALARM", "OK"])

    def test_published_anchor_cannot_disappear(self):
        events = sequence(["ALARM", "OK", "ALARM"])
        store = MemoryProjection()
        store.apply(events[0])
        with self.assertRaises(TransitionConflict):
            plan_projection([events[2]], list(store.rows.values()), alarm_key(events[2]), NOW, store.new_id)

    def test_same_transition_time_with_different_state_is_store_conflict(self):
        event = sequence(["ALARM"])[0]
        contradictory = replace(event, state="OK", dedupe_key="a" * 64)
        with self.assertRaises(TransitionConflict):
            plan_projection([event, contradictory], [], alarm_key(event), NOW, uuid.uuid4)

    def test_duplicate_with_different_predecessor_is_store_conflict(self):
        event = sequence(["ALARM"])[0]
        contradictory = replace(event, previous_state="INSUFFICIENT_DATA")
        with self.assertRaises(TransitionConflict):
            plan_projection([event, contradictory], [], alarm_key(event), NOW, uuid.uuid4)


class FakeDbError(Exception):
    def __init__(self, sqlstate, constraint=None):
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(constraint_name=constraint)


class TransactionBoundaryTests(unittest.TestCase):
    def make_store(self, outcomes, *, commit_error=None, max_attempts=4):
        calls = []

        @contextmanager
        def factory():
            calls.append("open")
            try:
                yield object()
                if commit_error is not None:
                    raise commit_error
                calls.append("commit")
            except Exception:
                calls.append("rollback")
                raise
            finally:
                calls.append("close")

        store = DsqlIncidentStore(factory, max_attempts=max_attempts, sleep=lambda _: calls.append("sleep"), jitter=lambda: 0.5)
        iterator = iter(outcomes)

        def attempt(*args):
            calls.append("read-compute-write")
            outcome = next(iterator)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        store._apply_transaction = attempt
        return store, calls

    def test_occ_retries_whole_transaction_after_rollback_and_close(self):
        expected = ApplyResult("created", "inc_" + "1" * 32)
        store, calls = self.make_store([FakeDbError("40001"), expected])
        raw = fixture()
        self.assertEqual(store.apply(transition(raw), raw), expected)
        self.assertEqual(calls, ["open", "read-compute-write", "rollback", "close", "sleep", "open", "read-compute-write", "commit", "close"])

    def test_retry_exhaustion_does_not_acknowledge_partial_result(self):
        store, calls = self.make_store([FakeDbError("40001"), FakeDbError("40001")], max_attempts=2)
        raw = fixture()
        with self.assertRaises(DsqlConcurrencyError):
            store.apply(transition(raw), raw)
        self.assertEqual(calls.count("rollback"), 2)
        self.assertNotIn("commit", calls)

    def test_validation_conflict_does_not_retry(self):
        store, calls = self.make_store([TransitionConflict("contradictory predecessor")])
        raw = fixture()
        with self.assertRaises(TransitionConflict):
            store.apply(transition(raw), raw)
        self.assertEqual(calls.count("open"), 1)

    def test_result_is_not_returned_if_commit_fails(self):
        store, calls = self.make_store([ApplyResult("created")], commit_error=FakeDbError("08006"))
        raw = fixture()
        with self.assertRaises(FakeDbError):
            store.apply(transition(raw), raw)
        self.assertNotIn("commit", calls)

    def test_only_known_unique_races_retry(self):
        self.assertTrue(_retryable(FakeDbError("23505", "pk_alarms")))
        self.assertTrue(_retryable(FakeDbError("23505", "pk_alarm_events")))
        self.assertFalse(_retryable(FakeDbError("23505", "unknown_constraint")))
        self.assertFalse(_retryable(FakeDbError("23503")))

    def test_mismatched_original_is_rejected_before_db_open(self):
        raw = fixture()
        normalized = transition(raw)
        raw["detail"]["state"]["value"] = "OK"
        store, calls = self.make_store([])
        with self.assertRaises(EventContractError):
            store.apply(normalized, raw)
        self.assertEqual(calls, [])

    def test_oversize_raw_value_rejected_before_db_open(self):
        raw = fixture()
        raw["detail"]["state"]["reason"] = "x" * (1024 * 1024)
        store, calls = self.make_store([])
        with self.assertRaises(DsqlProjectionTooLarge):
            store.apply(transition(raw), raw)
        self.assertEqual(calls, [])

    def test_non_json_number_is_rejected(self):
        raw = fixture()
        raw["extra"] = float("nan")
        with self.assertRaises(EventContractError):
            _validate_input(transition(raw), raw)


if __name__ == "__main__":
    unittest.main()
