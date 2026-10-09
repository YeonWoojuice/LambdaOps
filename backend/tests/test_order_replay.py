"""End-to-end local replay of valid CloudWatch transitions in every delivery order."""

import itertools
import json
from pathlib import Path
import unittest

from lambdaops_m3.contracts import parse_eventbridge_alarm
from lambdaops_m3.store import MemoryIncidentStore


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def transition(event):
    return parse_eventbridge_alarm(event, {event["resources"][0]}, "slash-api", "dev")


def later_alarm(previous_state, previous_at, at="2026-10-08T12:20:00.000+0000"):
    event = fixture("alarm.json")
    event["id"] = "74e764c5-4775-40fd-adfb-f084e93ef673"
    event["time"] = "2026-10-08T12:20:01Z"
    event["detail"]["state"]["timestamp"] = at
    event["detail"]["previousState"] = {"value": previous_state, "timestamp": previous_at}
    return event


class OrderReplayTests(unittest.TestCase):
    def replay_every_order(self, events, expected):
        for order in itertools.permutations(events):
            with self.subTest(order=tuple(event["id"] for event in order)):
                store = MemoryIncidentStore()
                for event in order:
                    store.apply(transition(event), event)
                incidents, _ = store.list_incidents()
                actual = sorted(
                    (
                        item["detectedAt"],
                        item["alarmState"],
                        item.get("alarmOkAt"),
                        tuple(
                            entry["state"]
                            for entry in store.get_incident(item["incidentId"])["stateHistory"]
                        ),
                    )
                    for item in incidents
                )
                self.assertEqual(actual, expected)
                for item in incidents:
                    self.assertEqual(item["incidentState"], "NEW")

    def test_alarm_ok_alarm_is_two_incidents_in_every_order(self):
        alarm = fixture("alarm.json")
        ok = fixture("ok.json")
        later = fixture("later_alarm.json")
        self.replay_every_order(
            [alarm, ok, later],
            [
                ("2026-10-08T12:00:00.123000Z", "OK", "2026-10-08T12:10:00.456000Z", ("ALARM", "OK")),
                ("2026-10-08T12:20:00.000000Z", "ALARM", None, ("ALARM",)),
            ],
        )

    def test_reverse_order_fixture_replays_two_episodes(self):
        sequence = fixture("out_of_order_replay.json")
        store = MemoryIncidentStore()
        for name in sequence["fixtureOrder"]:
            event = fixture(name)
            store.apply(transition(event), event)
        incidents, _ = store.list_incidents()
        self.assertEqual(len(incidents), 2)
        self.assertEqual(
            sorted(item["alarmState"] for item in incidents),
            ["ALARM", "OK"],
        )

    def test_alarm_insufficient_ok_alarm_is_two_incidents_in_every_order(self):
        alarm = fixture("alarm.json")
        insufficient = fixture("insufficient_data.json")
        ok = fixture("ok.json")
        ok["detail"]["previousState"] = {
            "value": "INSUFFICIENT_DATA",
            "timestamp": "2026-10-08T12:05:00.000+0000",
        }
        later = later_alarm("OK", "2026-10-08T12:10:00.456+0000")
        self.replay_every_order(
            [alarm, insufficient, ok, later],
            [
                ("2026-10-08T12:00:00.123000Z", "OK", "2026-10-08T12:10:00.456000Z", ("ALARM", "INSUFFICIENT_DATA", "OK")),
                ("2026-10-08T12:20:00.000000Z", "ALARM", None, ("ALARM",)),
            ],
        )

    def test_alarm_insufficient_alarm_ok_is_one_incident_in_every_order(self):
        alarm = fixture("alarm.json")
        insufficient = fixture("insufficient_data.json")
        returned = later_alarm("INSUFFICIENT_DATA", "2026-10-08T12:05:00.000+0000", "2026-10-08T12:07:00.000+0000")
        returned["time"] = "2026-10-08T12:07:01Z"
        ok = fixture("ok.json")
        ok["detail"]["previousState"] = {"value": "ALARM", "timestamp": "2026-10-08T12:07:00.000+0000"}
        self.replay_every_order(
            [alarm, insufficient, returned, ok],
            [
                ("2026-10-08T12:00:00.123000Z", "OK", "2026-10-08T12:10:00.456000Z", ("ALARM", "INSUFFICIENT_DATA", "ALARM", "OK")),
            ],
        )


if __name__ == "__main__":
    unittest.main()
