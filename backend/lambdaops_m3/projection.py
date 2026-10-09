"""Order-independent episode projection from immutable alarm transition records.

Each CloudWatch state-change event names its immediate previous state and state
transition time. Those references form a chain even when SQS delivers the events
in a different order. Missing predecessor events are represented as synthetic
nodes until their full EventBridge envelopes arrive.

This module only computes a projection. Persisting a new projection requires a
separate per-alarm revision check so concurrent ingesters cannot publish an
older view over a newer one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .contracts import ALARM_STATES, AlarmTransition


class ProjectionConflict(ValueError):
    """Transition evidence describes incompatible states or predecessor links."""


@dataclass(frozen=True)
class Episode:
    start_at: str
    last_state: str
    last_at: str
    alarm_ok_at: Optional[str]
    alarm_events: Tuple[AlarmTransition, ...]
    events: Tuple[AlarmTransition, ...]


@dataclass
class _Node:
    state: str
    at: str
    actual: Optional[AlarmTransition] = None
    previous: Optional[str] = None
    successor: Optional[str] = None


def project_episodes(transitions: Sequence[AlarmTransition]) -> List[Episode]:
    """Return known Alarm episodes, independent of transition arrival order.

    Input must be for one account, region, and Alarm ARN. Duplicate deliveries
    with the same state and transition timestamp collapse to one event; the
    lexicographically smallest EventBridge ID supplies its raw-event reference.
    An episode whose opening ALARM is only a synthetic predecessor has an empty
    ``alarm_events`` tuple until that ALARM's actual event arrives.
    """
    if not transitions:
        return []

    identity = (
        transitions[0].account,
        transitions[0].region,
        transitions[0].alarm_arn,
    )
    nodes: Dict[str, _Node] = {}
    for transition in transitions:
        if (transition.account, transition.region, transition.alarm_arn) != identity:
            raise ProjectionConflict("transitions must belong to one alarm")
        if transition.state not in ALARM_STATES:
            raise ProjectionConflict("unsupported alarm state")
        if (transition.previous_state is None) != (transition.previous_transitioned_at is None):
            raise ProjectionConflict("previous state and timestamp must occur together")
        if transition.previous_state is not None:
            if transition.previous_state not in ALARM_STATES:
                raise ProjectionConflict("unsupported previous alarm state")
            if transition.previous_transitioned_at >= transition.transitioned_at:
                raise ProjectionConflict("previous transition must be earlier")

        node = nodes.get(transition.transitioned_at)
        if node is None:
            node = _Node(transition.state, transition.transitioned_at)
            nodes[transition.transitioned_at] = node
        elif node.state != transition.state:
            raise ProjectionConflict("two states claim one transition timestamp")
        if node.actual is not None:
            old_previous = (
                node.actual.previous_state,
                node.actual.previous_transitioned_at,
            )
            new_previous = (
                transition.previous_state,
                transition.previous_transitioned_at,
            )
            if old_previous != new_previous:
                raise ProjectionConflict("duplicate transition has different predecessor")
        if node.actual is None or transition.event_id < node.actual.event_id:
            node.actual = transition

    # Add each predecessor as a synthetic node when its own event is missing.
    # The timestamp uniquely identifies one state transition for an alarm.
    for node in tuple(nodes.values()):
        transition = node.actual
        if transition is None or transition.previous_state is None:
            continue
        previous_at = transition.previous_transitioned_at
        previous = nodes.get(previous_at)
        if previous is None:
            previous = _Node(transition.previous_state, previous_at)
            nodes[previous_at] = previous
        elif previous.state != transition.previous_state:
            raise ProjectionConflict("previous state conflicts with a transition at that time")
        if node.previous is not None and node.previous != previous_at:
            raise ProjectionConflict("transition has two predecessors")
        node.previous = previous_at
        if previous.successor is not None and previous.successor != node.at:
            raise ProjectionConflict("one transition has two successors")
        previous.successor = node.at

    # `previousState` names the immediately preceding state transition. An
    # event between the two timestamps would contradict that evidence, even
    # when the intervening event belongs to a disconnected partial chain.
    ordered_at = sorted(nodes)
    for index, at in enumerate(ordered_at):
        previous_at = nodes[at].previous
        if previous_at is not None and (index == 0 or ordered_at[index - 1] != previous_at):
            raise ProjectionConflict("another state transition lies between a linked predecessor and successor")

    episodes: List[Episode] = []
    visited = set()
    for root in sorted(nodes.values(), key=lambda item: item.at):
        if root.previous is not None:
            continue
        current = root
        start_at: Optional[str] = None
        last_state: Optional[str] = None
        last_at: Optional[str] = None
        alarm_events: List[AlarmTransition] = []
        events: List[AlarmTransition] = []
        while current is not None:
            if current.at in visited:
                raise ProjectionConflict("cycle in alarm predecessor chain")
            visited.add(current.at)
            if current.state == "ALARM":
                if start_at is None:
                    start_at = current.at
                last_state, last_at = current.state, current.at
                if current.actual is not None:
                    alarm_events.append(current.actual)
                    events.append(current.actual)
            elif current.state == "INSUFFICIENT_DATA":
                if start_at is not None:
                    last_state, last_at = current.state, current.at
                    if current.actual is not None:
                        events.append(current.actual)
            else:  # OK terminates only this alarm episode.
                if start_at is not None:
                    if current.actual is not None:
                        events.append(current.actual)
                    episodes.append(
                        Episode(start_at, "OK", current.at, current.at, tuple(alarm_events), tuple(events))
                    )
                    start_at = last_state = last_at = None
                    alarm_events = []
                    events = []
            current = nodes[current.successor] if current.successor is not None else None

        if start_at is not None:
            episodes.append(
                Episode(start_at, last_state, last_at, None, tuple(alarm_events), tuple(events))
            )

    if len(visited) != len(nodes):
        raise ProjectionConflict("unreachable alarm transitions")
    episodes.sort(key=lambda episode: (episode.start_at, episode.last_at))
    return episodes
