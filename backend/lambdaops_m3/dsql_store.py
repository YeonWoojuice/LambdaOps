"""Atomic DSQL event ingestion with stable Incident IDs and bounded retries.

The injected factory returns a fresh psycopg connection with dict rows,
autocommit disabled and REPEATABLE READ isolation. Its context manager commits
only a complete attempt, rolls back failures and closes the connection.
"""

from __future__ import annotations

from dataclasses import fields
import json
import random
import time
from typing import Any, Callable, Dict, Mapping, Optional
import uuid

from .contracts import AlarmTransition, EventContractError, parse_eventbridge_alarm
from .dsql_projection import plan_projection, utc_text
from .dsql_queries import DsqlReadMixin
from .store import ApplyResult, TransitionConflict, alarm_key


MAX_CHANGED_ROWS = 3000
MAX_TRANSACTION_BYTES = 10 * 1024 * 1024
MAX_COLUMN_BYTES = 1024 * 1024
# A conservative per-operation allowance; the service still enforces its exact
# physical-size limits. A service rejection rolls back the entire transaction.
ROW_OVERHEAD_BYTES = 1024

EVENT_COLUMNS = """dedupe_key, alarm_id, eventbridge_event_id, state,
    transitioned_at, previous_state, previous_transitioned_at, service, environment"""
INCIDENT_COLUMNS = """incident_id, alarm_id, anchor_event_key, service, environment,
    detected_at, alarm_state, incident_state, last_state_transition_at, alarm_ok_at,
    created_at, updated_at, redirect_to_incident_id"""
MUTABLE_FIELDS = (
    "anchor_event_key", "service", "environment", "detected_at", "alarm_state",
    "incident_state", "last_state_transition_at", "alarm_ok_at",
)
TIME_FIELDS = {"detected_at", "last_state_transition_at", "alarm_ok_at"}
RACE_CONSTRAINTS = {
    "pk_alarms", "pk_alarm_events", "uq_alarm_events_alarm_key",
    "uq_alarm_events_alarm_transition", "uq_incidents_canonical_anchor",
}


class DsqlConcurrencyError(RuntimeError):
    """The complete operation exhausted its bounded transaction retries."""


class DsqlProjectionTooLarge(RuntimeError):
    """The operation cannot be safely attempted as one DSQL transaction."""


class _RevisionChanged(RuntimeError):
    pass


def _retryable(exc: Exception) -> bool:
    # DSQL OC000/OC001 are diagnostic messages; both use SQLSTATE 40001.
    if isinstance(exc, _RevisionChanged) or getattr(exc, "sqlstate", None) == "40001":
        return True
    if getattr(exc, "sqlstate", None) == "23505":
        return getattr(getattr(exc, "diag", None), "constraint_name", None) in RACE_CONSTRAINTS
    return False


def _canonical(row: Mapping[str, Any], identity: Mapping[str, Any]) -> AlarmTransition:
    return AlarmTransition(
        event_id=row["eventbridge_event_id"],
        account=identity["account_id"], region=identity["region"],
        alarm_arn=identity["alarm_arn"], alarm_name=identity["alarm_name"],
        state=row["state"], transitioned_at=utc_text(row["transitioned_at"]),
        service=row["service"], environment=row["environment"],
        dedupe_key=row["dedupe_key"], previous_state=row["previous_state"],
        previous_transitioned_at=utc_text(row["previous_transitioned_at"]),
    )


def _validate_input(transition: AlarmTransition, raw_event: Dict[str, Any]) -> str:
    parsed = parse_eventbridge_alarm(
        raw_event, {transition.alarm_arn}, transition.service, transition.environment
    )
    if any(getattr(parsed, field.name) != getattr(transition, field.name) for field in fields(AlarmTransition)):
        raise EventContractError("normalized transition does not match its original event")
    for name, length in (("account", 12), ("region", 32), ("alarm_name", 255), ("service", 100), ("environment", 32)):
        value = getattr(transition, name)
        if not value.strip() or len(value) > length or "\x00" in value:
            raise EventContractError("%s exceeds the approved storage contract" % name)
    try:
        encoded = json.dumps(raw_event, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EventContractError("raw event must be valid JSON") from exc
    for value in (transition.alarm_arn, transition.event_id, encoded):
        if len(value.encode("utf-8")) > MAX_COLUMN_BYTES:
            raise DsqlProjectionTooLarge("a non-indexed value exceeds 1 MiB")
    return encoded


def _same_projection(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    for name in MUTABLE_FIELDS:
        left, right = old[name], new[name]
        if name in TIME_FIELDS:
            left = utc_text(left) if left is not None else None
            right = utc_text(right) if right is not None else None
        if left != right:
            return False
    return True


class DsqlIncidentStore(DsqlReadMixin):
    supports_page_queries = True

    def __init__(
        self, connection_factory: Callable[[], Any], *, max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        id_factory: Callable[[], Any] = uuid.uuid4,
    ) -> None:
        if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        self.connection_factory = connection_factory
        self.max_attempts, self.sleep, self.jitter, self.id_factory = max_attempts, sleep, jitter, id_factory

    def apply(self, transition: AlarmTransition, raw_event: Dict[str, Any]) -> ApplyResult:
        encoded = _validate_input(transition, raw_event)
        for attempt in range(self.max_attempts):
            try:
                with self.connection_factory() as connection:
                    result = self._apply_transaction(connection, transition, encoded)
                # This runs only after a successful commit, never before it.
                return result
            except Exception as exc:
                if not _retryable(exc):
                    raise
                if attempt + 1 == self.max_attempts:
                    raise DsqlConcurrencyError("DSQL transaction retries exhausted") from exc
                self.sleep(self.jitter() * min(0.5, 0.025 * (2 ** attempt)))
        raise AssertionError("unreachable retry state")

    def _apply_transaction(self, connection: Any, transition: AlarmTransition, raw_json: str) -> ApplyResult:
        key = alarm_key(transition)
        duplicate = connection.execute(
            """SELECT e.dedupe_key, e.alarm_id, e.state, e.transitioned_at,
                      e.previous_state, e.previous_transitioned_at,
                      a.account_id, a.region, a.alarm_arn
               FROM public.alarm_events e JOIN public.alarms a ON a.alarm_id = e.alarm_id
               WHERE e.dedupe_key = %s""", (transition.dedupe_key,),
        ).fetchone()
        if duplicate is not None:
            actual = (
                duplicate["alarm_id"], duplicate["account_id"], duplicate["region"],
                duplicate["alarm_arn"], duplicate["state"], utc_text(duplicate["transitioned_at"]),
                duplicate["previous_state"], utc_text(duplicate["previous_transitioned_at"]),
            )
            expected = (
                key, transition.account, transition.region, transition.alarm_arn,
                transition.state, transition.transitioned_at,
                transition.previous_state, transition.previous_transitioned_at,
            )
            if actual != expected:
                raise TransitionConflict("duplicate transition has conflicting immutable evidence")
            return ApplyResult("duplicate")

        alarm = connection.execute("SELECT * FROM public.alarms WHERE alarm_id = %s", (key,)).fetchone()
        new_alarm = alarm is None
        if new_alarm:
            alarm = {
                "alarm_id": key, "account_id": transition.account, "region": transition.region,
                "alarm_arn": transition.alarm_arn, "alarm_name": transition.alarm_name, "revision": 0,
            }
            connection.execute(
                """INSERT INTO public.alarms (alarm_id, account_id, region, alarm_arn, alarm_name)
                   VALUES (%s, %s, %s, %s, %s)""",
                tuple(alarm[name] for name in ("alarm_id", "account_id", "region", "alarm_arn", "alarm_name")),
            )
        elif (alarm["account_id"], alarm["region"], alarm["alarm_arn"], alarm["alarm_name"]) != (
            transition.account, transition.region, transition.alarm_arn, transition.alarm_name
        ):
            raise TransitionConflict("stored Alarm identity differs from incoming evidence")

        records = connection.execute(
            "SELECT " + EVENT_COLUMNS + " FROM public.alarm_events WHERE alarm_id = %s ORDER BY transitioned_at",
            (key,),
        ).fetchall()
        transitions = [_canonical(row, alarm) for row in records] + [transition]
        old_rows = connection.execute(
            "SELECT " + INCIDENT_COLUMNS + " FROM public.incidents WHERE alarm_id = %s", (key,),
        ).fetchall()
        now = connection.execute("SELECT CURRENT_TIMESTAMP AS now").fetchone()["now"]
        plan = plan_projection(transitions, old_rows, key, now, self.id_factory)
        old = {row["incident_id"]: row for row in old_rows}
        redirects = {
            incident_id: target for incident_id, target in plan.redirects.items()
            if old[incident_id]["redirect_to_incident_id"] != target
        }
        changes = {
            incident_id: row for incident_id, row in plan.canonical.items()
            if incident_id not in old or not _same_projection(old[incident_id], row)
        }
        changed_rows = int(new_alarm) + 2 + len(redirects) + len(changes)
        estimate = len(raw_json.encode("utf-8")) + sum(
            len(json.dumps(row, default=str, ensure_ascii=False).encode("utf-8"))
            for row in changes.values()
        ) + changed_rows * ROW_OVERHEAD_BYTES
        if changed_rows > MAX_CHANGED_ROWS or estimate > MAX_TRANSACTION_BYTES:
            raise DsqlProjectionTooLarge("projection exceeds the single-transaction safety bound")

        connection.execute(
            """INSERT INTO public.alarm_events
               (dedupe_key, alarm_id, eventbridge_event_id, state, transitioned_at,
                previous_state, previous_transitioned_at, service, environment, raw_event)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)""",
            (transition.dedupe_key, key, transition.event_id, transition.state, transition.transitioned_at,
             transition.previous_state, transition.previous_transitioned_at, transition.service,
             transition.environment, raw_json),
        )
        # Releasing losing anchors first is essential for the partial UNIQUE index.
        for incident_id, target in redirects.items():
            connection.execute(
                """UPDATE public.incidents SET redirect_to_incident_id = %s, updated_at = %s
                   WHERE alarm_id = %s AND incident_id = %s""", (target, now, key, incident_id),
            )
        for incident_id, row in changes.items():
            if incident_id not in old:
                connection.execute(
                    """INSERT INTO public.incidents
                       (incident_id, alarm_id, anchor_event_key, service, environment, detected_at,
                        alarm_state, incident_state, last_state_transition_at, alarm_ok_at, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    tuple(row[name] for name in (
                        "incident_id", "alarm_id", "anchor_event_key", "service", "environment", "detected_at",
                        "alarm_state", "incident_state", "last_state_transition_at", "alarm_ok_at", "created_at", "updated_at",
                    )),
                )
            else:
                connection.execute(
                    """UPDATE public.incidents SET anchor_event_key = %s, service = %s, environment = %s,
                       detected_at = %s, alarm_state = %s, incident_state = %s,
                       last_state_transition_at = %s, alarm_ok_at = %s, updated_at = %s
                       WHERE alarm_id = %s AND incident_id = %s""",
                    tuple(row[name] for name in MUTABLE_FIELDS) + (now, key, incident_id),
                )
        latest = max(transitions, key=lambda item: item.transitioned_at)
        updated = connection.execute(
            """UPDATE public.alarms SET latest_state = %s, latest_transition_at = %s, revision = revision + 1
               WHERE alarm_id = %s AND revision = %s""",
            (latest.state, latest.transitioned_at, key, alarm["revision"]),
        )
        if updated.rowcount != 1:
            raise _RevisionChanged("Alarm changed during the attempt")
        for incident_id, episode in plan.episodes.items():
            if any(event.dedupe_key == transition.dedupe_key for event in episode.events):
                if incident_id in old:
                    return ApplyResult("updated", incident_id)
                outcome = "created" if transition.state == "ALARM" and episode.last_at == transition.transitioned_at else "reconstructed"
                return ApplyResult(outcome, incident_id)
        return ApplyResult("recorded")
