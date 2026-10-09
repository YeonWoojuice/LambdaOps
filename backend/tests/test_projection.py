from dataclasses import replace
import itertools
import json
from pathlib import Path
import unittest

from lambdaops_m3.contracts import parse_eventbridge_alarm
from lambdaops_m3.projection import ProjectionConflict, project_episodes


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def transition(event):
    return parse_eventbridge_alarm(event, {event["resources"][0]}, "slash-api", "dev")


def later_alarm(after_state, after_at, at="2026-10-08T12:20:00.000+0000"):
    event = fixture("alarm.json")
    event["id"] = "74e764c5-4775-40fd-adfb-f084e93ef673"
    event["time"] = "2026-10-08T12:20:01Z"
    event["detail"]["state"]["timestamp"] = at
    event["detail"]["previousState"] = {
        "value": after_state,
        "timestamp": after_at,
    }
    return event


class EpisodeProjectionTests(unittest.TestCase):
    def assert_all_permutations(self, transitions, expected):
        canonical = None
        for order in itertools.permutations(transitions):
            episodes = project_episodes(order)
            summary = [
                (
                    episode.start_at,
                    episode.last_state,
                    episode.last_at,
                    episode.alarm_ok_at,
                    tuple(event.event_id for event in episode.alarm_events),
                    tuple(event.state for event in episode.events),
                )
                for episode in episodes
            ]
            self.assertEqual(summary, expected)
            if canonical is None:
                canonical = episodes
            else:
                self.assertEqual(episodes, canonical)

    def test_alarm_ok_alarm_all_delivery_orders(self):
        first = transition(fixture("alarm.json"))
        ok = transition(fixture("ok.json"))
        second = transition(later_alarm("OK", "2026-10-08T12:10:00.456+0000"))
        self.assert_all_permutations(
            [first, ok, second],
            [
                (
                    first.transitioned_at,
                    "OK",
                    ok.transitioned_at,
                    ok.transitioned_at,
                    (first.event_id,),
                    ("ALARM", "OK"),
                ),
                (
                    second.transitioned_at,
                    "ALARM",
                    second.transitioned_at,
                    None,
                    (second.event_id,),
                    ("ALARM",),
                ),
            ],
        )

    def test_alarm_insufficient_ok_alarm_all_delivery_orders(self):
        first = transition(fixture("alarm.json"))
        insufficient = transition(fixture("insufficient_data.json"))
        ok_event = fixture("ok.json")
        ok_event["detail"]["previousState"] = {
            "value": "INSUFFICIENT_DATA",
            "timestamp": "2026-10-08T12:05:00.000+0000",
        }
        ok = transition(ok_event)
        second = transition(later_alarm("OK", "2026-10-08T12:10:00.456+0000"))
        self.assert_all_permutations(
            [first, insufficient, ok, second],
            [
                (
                    first.transitioned_at,
                    "OK",
                    ok.transitioned_at,
                    ok.transitioned_at,
                    (first.event_id,),
                    ("ALARM", "INSUFFICIENT_DATA", "OK"),
                ),
                (
                    second.transitioned_at,
                    "ALARM",
                    second.transitioned_at,
                    None,
                    (second.event_id,),
                    ("ALARM",),
                ),
            ],
        )

    def test_alarm_after_insufficient_is_same_episode_for_every_order(self):
        first = transition(fixture("alarm.json"))
        insufficient = transition(fixture("insufficient_data.json"))
        returned_event = later_alarm(
            "INSUFFICIENT_DATA",
            "2026-10-08T12:05:00.000+0000",
            "2026-10-08T12:07:00.000+0000",
        )
        returned_event["time"] = "2026-10-08T12:07:01Z"
        returned = transition(returned_event)
        ok_event = fixture("ok.json")
        ok_event["detail"]["previousState"] = {
            "value": "ALARM",
            "timestamp": "2026-10-08T12:07:00.000+0000",
        }
        ok = transition(ok_event)
        self.assert_all_permutations(
            [first, insufficient, returned, ok],
            [
                (
                    first.transitioned_at,
                    "OK",
                    ok.transitioned_at,
                    ok.transitioned_at,
                    (first.event_id, returned.event_id),
                    ("ALARM", "INSUFFICIENT_DATA", "ALARM", "OK"),
                )
            ],
        )

    def test_missing_alarm_is_a_synthetic_predecessor(self):
        insufficient = transition(fixture("insufficient_data.json"))
        episode = project_episodes([insufficient])[0]
        self.assertEqual(episode.start_at, "2026-10-08T12:00:00.123000Z")
        self.assertEqual(episode.last_state, "INSUFFICIENT_DATA")
        self.assertEqual(episode.alarm_events, ())
        self.assertEqual(episode.events, (insufficient,))

    def test_duplicate_delivery_collapses_deterministically(self):
        alarm = transition(fixture("alarm.json"))
        duplicate = replace(alarm, event_id="00000000-0000-0000-0000-000000000000")
        projected = project_episodes([alarm, duplicate])
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0].alarm_events, (duplicate,))
        self.assertEqual(projected, project_episodes([duplicate, alarm]))

    def test_conflicting_evidence_is_rejected(self):
        alarm = transition(fixture("alarm.json"))
        with self.assertRaises(ProjectionConflict):
            project_episodes([alarm, replace(alarm, state="OK")])

        second = transition(later_alarm("OK", "2026-10-08T12:10:00.456+0000"))
        branched = replace(
            second,
            event_id="dd801a1d-754e-4c3a-b411-d4a42876294d",
            transitioned_at="2026-10-08T12:21:00.000000Z",
        )
        with self.assertRaises(ProjectionConflict):
            project_episodes([second, branched])

        mixed_alarm = replace(alarm, alarm_arn=alarm.alarm_arn + "-other")
        with self.assertRaises(ProjectionConflict):
            project_episodes([alarm, mixed_alarm])

        # OK says its immediately previous transition was the 12:00 ALARM,
        # so the separate 12:02 ALARM fixture cannot be a valid predecessor.
        ok = transition(fixture("ok.json"))
        contradictory = transition(fixture("stale_alarm.json"))
        with self.assertRaises(ProjectionConflict):
            project_episodes([ok, contradictory])


if __name__ == "__main__":
    unittest.main()
