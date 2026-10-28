"""Snapshot-consistent reads of canonical Incidents and their episode evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .contracts import AlarmTransition
from .projection import project_episodes
from .query_contract import IncidentQuery, SORT_COLUMNS


class ReadIntegrityError(RuntimeError):
    """Persisted summaries or ID redirects disagree with their evidence."""


_SUMMARY_COLUMNS = """
    i.incident_id, i.alarm_id, i.anchor_event_key, i.service, i.environment,
    i.detected_at, i.alarm_state, i.incident_state, i.last_state_transition_at,
    i.alarm_ok_at, i.created_at, i.updated_at, i.redirect_to_incident_id,
    a.account_id, a.region, a.alarm_arn, a.alarm_name,
    anchor.eventbridge_event_id AS original_event_ref
"""
_SUMMARY_FROM = """
    FROM public.incidents i
    JOIN public.alarms a ON a.alarm_id = i.alarm_id
    JOIN public.alarm_events anchor
      ON anchor.alarm_id = i.alarm_id AND anchor.dedupe_key = i.anchor_event_key
"""


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ReadIntegrityError("Stored timestamp has no timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _summary(row: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "incidentId": row["incident_id"],
        "account": row["account_id"],
        "region": row["region"],
        "service": row["service"],
        "environment": row["environment"],
        "alarmArn": row["alarm_arn"],
        "alarmName": row["alarm_name"],
        "detectedAt": _timestamp(row["detected_at"]),
        "alarmState": row["alarm_state"],
        "incidentState": row["incident_state"],
        "lastStateTransitionAt": _timestamp(row["last_state_transition_at"]),
        "originalEventRef": row["original_event_ref"],
        "createdAt": _timestamp(row["created_at"]),
        "updatedAt": _timestamp(row["updated_at"]),
    }
    if row["alarm_ok_at"] is not None:
        result["alarmOkAt"] = _timestamp(row["alarm_ok_at"])
    return result


def _filters(query: IncidentQuery):
    clauses = ["i.redirect_to_incident_id IS NULL"]
    values = []
    for column, value in (
        ("i.service", query.service), ("i.environment", query.environment),
        ("i.alarm_state", query.alarm_state), ("i.incident_state", query.incident_state),
    ):
        if value is not None:
            clauses.append(column + " = %s")
            values.append(value)
    if query.from_at is not None:
        clauses.append("i.detected_at >= %s")
        values.append(query.from_at)
    if query.to_at is not None:
        clauses.append("i.detected_at < %s")
        values.append(query.to_at)
    if query.search is not None:
        # '!' is the explicit LIKE escape character; user %, _ and ! stay literal.
        pattern = "%" + query.search.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        clauses.append("(" + " OR ".join(
            column + " ILIKE %s ESCAPE '!'"
            for column in ("i.incident_id", "a.alarm_name", "i.service", "i.environment")
        ) + ")")
        values.extend([pattern] * 4)
    return " WHERE " + " AND ".join(clauses), values


class DsqlReadMixin:
    """Uses a supplied dict-row connection with REPEATABLE READ isolation.

    Each context owns one short transaction and closes its connection. List/count
    and detail/evidence therefore share a snapshot; HTTP page requests do not.
    """

    supports_page_queries = True

    def list_incidents_page(self, query: IncidentQuery) -> Dict[str, Any]:
        where, values = _filters(query)
        # Both identifiers come from server allowlists, never interpolated input.
        order = SORT_COLUMNS[query.sort_by]
        direction = {"asc": "ASC", "desc": "DESC"}[query.sort_order]
        with self.connection_factory() as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS total " + _SUMMARY_FROM + where, values
            ).fetchone()["total"]
            rows = connection.execute(
                "SELECT " + _SUMMARY_COLUMNS + _SUMMARY_FROM + where
                + " ORDER BY " + order + " " + direction + ", i.incident_id " + direction
                + " LIMIT %s OFFSET %s",
                values + [query.page_size, (query.page - 1) * query.page_size],
            ).fetchall()
            pages = (count + query.page_size - 1) // query.page_size
            return {
                "items": [_summary(row) for row in rows],
                "page": query.page,
                "pageSize": query.page_size,
                "totalItems": count,
                "totalPages": pages,
                "hasNextPage": query.page < pages,
            }

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        with self.connection_factory() as connection:
            row = connection.execute(
                "SELECT " + _SUMMARY_COLUMNS + _SUMMARY_FROM + " WHERE i.incident_id = %s",
                (incident_id,),
            ).fetchone()
            if row is None:
                return None
            expected_alarm = row["alarm_id"]
            seen = set()
            while row["redirect_to_incident_id"] is not None:
                if row["incident_id"] in seen or len(seen) >= 16:
                    raise ReadIntegrityError("Invalid Incident redirect chain")
                seen.add(row["incident_id"])
                row = connection.execute(
                    "SELECT " + _SUMMARY_COLUMNS + _SUMMARY_FROM + " WHERE i.incident_id = %s",
                    (row["redirect_to_incident_id"],),
                ).fetchone()
                if row is None or row["alarm_id"] != expected_alarm:
                    raise ReadIntegrityError("Invalid Incident redirect target")

            # Restrict both normalized and raw reads to this episode's time span.
            # Projecting normalized evidence also verifies that a stale summary
            # cannot cause raw events from an unrelated episode to be returned.
            events = connection.execute(
                """SELECT dedupe_key, eventbridge_event_id, state, transitioned_at,
                          previous_state, previous_transitioned_at, service, environment
                   FROM public.alarm_events
                   WHERE alarm_id = %s AND transitioned_at >= %s AND transitioned_at <= %s
                   ORDER BY transitioned_at, dedupe_key""",
                (row["alarm_id"], row["detected_at"], row["last_state_transition_at"]),
            ).fetchall()
            transitions = [AlarmTransition(
                event_id=event["eventbridge_event_id"], account=row["account_id"],
                region=row["region"], alarm_arn=row["alarm_arn"], alarm_name=row["alarm_name"],
                state=event["state"], transitioned_at=_timestamp(event["transitioned_at"]),
                service=event["service"], environment=event["environment"],
                dedupe_key=event["dedupe_key"], previous_state=event["previous_state"],
                previous_transitioned_at=_timestamp(event["previous_transitioned_at"]),
            ) for event in events]
            episodes = [episode for episode in project_episodes(transitions)
                        if any(event.dedupe_key == row["anchor_event_key"]
                               for event in episode.alarm_events)]
            if len(episodes) != 1:
                raise ReadIntegrityError("Incident anchor has no unique episode")
            episode = episodes[0]
            # A final synthetic state may be supported by the next episode's
            # predecessor reference outside this bounded read. Validate the actual
            # evidence and anchor here without inventing an event for that state.
            if not episode.events or episode.start_at != _timestamp(row["detected_at"]):
                raise ReadIntegrityError("Incident start disagrees with evidence")
            keys = [event.dedupe_key for event in episode.events]
            raw_rows = connection.execute(
                """SELECT eventbridge_event_id, state, transitioned_at, raw_event
                   FROM public.alarm_events WHERE alarm_id = %s AND dedupe_key IN ("""
                + ",".join(["%s"] * len(keys)) + ") ORDER BY transitioned_at, dedupe_key",
                [row["alarm_id"]] + keys,
            ).fetchall()
            if len(raw_rows) != len(keys):
                raise ReadIntegrityError("Incident evidence is missing")
            result = _summary(row)
            result.update({
                "requestedIncidentId": incident_id,
                "redirected": incident_id != row["incident_id"],
                "stateHistory": [{
                    "eventId": event["eventbridge_event_id"], "state": event["state"],
                    "transitionedAt": _timestamp(event["transitioned_at"]),
                    "rawEvent": event["raw_event"],
                } for event in raw_rows],
            })
            return result
