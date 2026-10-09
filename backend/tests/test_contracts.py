import copy
import json
from pathlib import Path
import unittest

from lambdaops_m3.contracts import EventContractError, parse_eventbridge_alarm


FIXTURES = Path(__file__).parent / "fixtures"
ALARM_ARN = (
    "arn:aws:cloudwatch:ap-northeast-2:123456789012:alarm:"
    "lambdaops-dev-slash-api-api-5xx-rate"
)


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def parse(event, allowed=None):
    return parse_eventbridge_alarm(
        event,
        {ALARM_ARN} if allowed is None else allowed,
        "slash-api",
        "dev",
    )


class AlarmContractTests(unittest.TestCase):
    def test_fixture_states_and_canonical_timestamps(self):
        expected = {
            "alarm.json": ("ALARM", "2026-10-08T12:00:00.123000Z"),
            "ok.json": ("OK", "2026-10-08T12:10:00.456000Z"),
            "insufficient_data.json": (
                "INSUFFICIENT_DATA",
                "2026-10-08T12:05:00.000000Z",
            ),
        }
        for name, (state, at) in expected.items():
            with self.subTest(name=name):
                transition = parse(fixture(name))
                self.assertEqual(transition.state, state)
                self.assertEqual(transition.transitioned_at, at)
                self.assertEqual(transition.alarm_arn, ALARM_ARN)
                self.assertEqual(transition.account, "123456789012")
                self.assertEqual(transition.region, "ap-northeast-2")
                self.assertEqual(transition.service, "slash-api")
                self.assertEqual(transition.environment, "dev")
                self.assertEqual(transition.raw_event_ref, transition.event_id)

    def test_duplicate_event_has_same_key(self):
        alarm = parse(fixture("alarm.json"))
        duplicate = parse(fixture("duplicate_alarm.json"))
        self.assertEqual(alarm.dedupe_key, duplicate.dedupe_key)

        repeated_transition = fixture("alarm.json")
        repeated_transition["id"] = "ecad3ae6-3b09-42fd-b4e7-5b4ca1086720"
        self.assertEqual(alarm.dedupe_key, parse(repeated_transition).dedupe_key)

    def test_stale_alarm_is_older_than_ok_with_a_distinct_key(self):
        alarm = parse(fixture("alarm.json"))
        ok = parse(fixture("ok.json"))
        stale = parse(fixture("stale_alarm.json"))
        self.assertLess(alarm.transitioned_at, stale.transitioned_at)
        self.assertLess(stale.transitioned_at, ok.transitioned_at)
        self.assertNotEqual(stale.event_id, alarm.event_id)
        self.assertNotEqual(stale.dedupe_key, alarm.dedupe_key)

    def test_timezone_offsets_normalize_to_same_instant_and_key(self):
        base = fixture("alarm.json")
        transition = parse(base)
        for timestamp in (
            "2026-10-08T21:00:00.123+09:00",
            "2026-10-08T21:00:00.123+0900",
            "2026-10-08T12:00:00.123Z",
        ):
            with self.subTest(timestamp=timestamp):
                event = copy.deepcopy(base)
                event["detail"]["state"]["timestamp"] = timestamp
                other = parse(event)
                self.assertEqual(other.transitioned_at, transition.transitioned_at)
                self.assertEqual(other.dedupe_key, transition.dedupe_key)

    def test_eventbridge_delivery_time_is_not_transition_time(self):
        event = fixture("alarm.json")
        event["time"] = "2026-10-08T13:00:00Z"
        self.assertEqual(parse(event).dedupe_key, parse(fixture("alarm.json")).dedupe_key)

    def test_key_changes_with_each_identity_component(self):
        base = fixture("alarm.json")
        baseline = parse(base).dedupe_key

        state = copy.deepcopy(base)
        state["detail"]["state"]["value"] = "OK"
        self.assertNotEqual(parse(state).dedupe_key, baseline)

        timestamp = copy.deepcopy(base)
        timestamp["detail"]["state"]["timestamp"] = "2026-10-08T12:00:01.123+0000"
        self.assertNotEqual(parse(timestamp).dedupe_key, baseline)

        alarm = copy.deepcopy(base)
        alarm["detail"]["alarmName"] = "lambdaops-dev-slash-api-api-average-latency"
        alarm["resources"] = [
            "arn:aws:cloudwatch:ap-northeast-2:123456789012:alarm:"
            "lambdaops-dev-slash-api-api-average-latency"
        ]
        self.assertNotEqual(parse(alarm, {alarm["resources"][0]}).dedupe_key, baseline)

        account = copy.deepcopy(base)
        account["account"] = "210987654321"
        account["resources"] = [ALARM_ARN.replace("123456789012", "210987654321")]
        self.assertNotEqual(parse(account, {account["resources"][0]}).dedupe_key, baseline)

        region = copy.deepcopy(base)
        region["region"] = "us-east-1"
        region["resources"] = [ALARM_ARN.replace("ap-northeast-2", "us-east-1")]
        self.assertNotEqual(parse(region, {region["resources"][0]}).dedupe_key, baseline)

    def test_rejects_unrelated_or_inconsistent_events(self):
        changes = {
            "source": ("source", "aws.ec2"),
            "detail type": ("detail-type", "CloudWatch Alarm Configuration Change"),
            "account": ("account", "210987654321"),
            "region": ("region", "us-east-1"),
            "resources": ("resources", []),
        }
        for label, (field, value) in changes.items():
            with self.subTest(label=label):
                event = fixture("alarm.json")
                event[field] = value
                with self.assertRaises(EventContractError):
                    parse(event)

        event = fixture("alarm.json")
        event["detail"]["alarmName"] = "some-other-alarm"
        with self.assertRaises(EventContractError):
            parse(event)

        with self.assertRaises(EventContractError):
            parse(fixture("alarm.json"), set())

    def test_rejects_invalid_state_or_timestamp(self):
        for value in ("RESOLVED", None, ["ALARM"]):
            with self.subTest(value=value):
                event = fixture("alarm.json")
                event["detail"]["state"]["value"] = value
                with self.assertRaises(EventContractError):
                    parse(event)

        missing_previous = fixture("alarm.json")
        missing_previous["detail"].pop("previousState")
        with self.assertRaises(EventContractError):
            parse(missing_previous)

        for value in (
            "2026-10-08T12:00:00",
            "2026-10-08T99:00:00Z",
            "2026-10-08T12:00:00.1234567Z",
            "not-a-date",
        ):
            with self.subTest(timestamp=value):
                event = fixture("alarm.json")
                event["detail"]["state"]["timestamp"] = value
                with self.assertRaises(EventContractError):
                    parse(event)


if __name__ == "__main__":
    unittest.main()
