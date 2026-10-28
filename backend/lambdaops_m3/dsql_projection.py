"""Stable Incident identity for the approved three-table DSQL model.

The shared projector determines episodes from evidence. This module assigns
already-published IDs to those episodes without changing their identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Sequence

from .contracts import AlarmTransition
from .projection import Episode, ProjectionConflict, project_episodes
from .store import TransitionConflict


def utc_text(value: Any) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TransitionConflict("stored timestamp must include a timezone")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class ProjectionPlan:
    canonical: Dict[str, Dict[str, Any]]
    redirects: Dict[str, str]
    episodes: Dict[str, Episode]


def plan_projection(
    transitions: Sequence[AlarmTransition],
    existing: Sequence[Mapping[str, Any]],
    alarm_id: str,
    now: Any,
    id_factory: Callable[[], Any],
) -> ProjectionPlan:
    """Retain IDs, select the oldest existing representative, flatten redirects.

    ``created_at`` is the original DB creation time, not the (correctable)
    occurrence time. A missing predecessor may establish an earlier start time
    than the earliest actual ALARM event used as the anchor.
    """
    try:
        episodes = [episode for episode in project_episodes(transitions) if episode.alarm_events]
    except ProjectionConflict as exc:
        raise TransitionConflict(str(exc)) from exc
    old = {row["incident_id"]: dict(row) for row in existing}
    if len(old) != len(existing):
        raise TransitionConflict("duplicate stored Incident identity")
    if any(row["alarm_id"] != alarm_id for row in old.values()):
        raise TransitionConflict("stored Incident belongs to another Alarm")

    canonical: Dict[str, Dict[str, Any]] = {}
    by_id: Dict[str, Episode] = {}
    anchor_targets: Dict[str, str] = {}
    for episode in episodes:
        keys = {event.dedupe_key for event in episode.alarm_events}
        candidates = [
            row for row in old.values()
            if row["redirect_to_incident_id"] is None and row["anchor_event_key"] in keys
        ]
        if candidates:
            winner = min(candidates, key=lambda row: (utc_text(row["created_at"]), row["incident_id"]))
            incident_id, created_at = winner["incident_id"], winner["created_at"]
        else:
            generated = id_factory()
            incident_id = "inc_" + (generated.hex if hasattr(generated, "hex") else str(generated))
            created_at = now
        if incident_id in canonical or (not candidates and incident_id in old):
            raise TransitionConflict("Incident ID generator reused an existing identity")
        first = episode.alarm_events[0]
        canonical[incident_id] = {
            "incident_id": incident_id,
            "alarm_id": alarm_id,
            "anchor_event_key": first.dedupe_key,
            "service": first.service,
            "environment": first.environment,
            "detected_at": episode.start_at,
            "alarm_state": episode.last_state,
            "incident_state": "NEW",
            "last_state_transition_at": episode.last_at,
            "alarm_ok_at": episode.alarm_ok_at,
            "created_at": created_at,
            "updated_at": now,
            "redirect_to_incident_id": None,
        }
        by_id[incident_id] = episode
        for key in keys:
            if key in anchor_targets:
                raise TransitionConflict("one actual ALARM belongs to two episodes")
            anchor_targets[key] = incident_id

    redirects: Dict[str, str] = {}
    for incident_id, row in old.items():
        target = anchor_targets.get(row["anchor_event_key"])
        if target is None:
            raise TransitionConflict("a published ALARM disappeared from the event ledger")
        if incident_id != target:
            redirects[incident_id] = target
        elif row["redirect_to_incident_id"] is not None:
            raise TransitionConflict("a redirect cannot become a representative")
    return ProjectionPlan(canonical, redirects, by_id)
