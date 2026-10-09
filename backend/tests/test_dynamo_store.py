import base64
import copy
import itertools
import json
from pathlib import Path
import unittest

from lambdaops_m3.contracts import parse_eventbridge_alarm
from lambdaops_m3.store import DynamoIncidentStore, TransitionConflict


FIXTURES = Path(__file__).parent / "fixtures"
ALARM_ARN = (
    "arn:aws:cloudwatch:ap-northeast-2:123456789012:alarm:"
    "lambdaops-dev-slash-api-api-5xx-rate"
)


class TransactionCanceled(Exception):
    response = {"Error": {"Code": "TransactionCanceledException"}}


class FakeDynamoClient:
    """Small transactional test double for the AWS adapter boundary."""

    def __init__(self):
        self.items = {}
        self.fail_next_transaction = False
        self.query_page_size = None

    @staticmethod
    def key(item):
        return item["PK"]["S"], item["SK"]["S"]

    def get_item(self, **kwargs):
        item = self.items.get(self.key(kwargs["Key"]))
        return {"Item": copy.deepcopy(item)} if item else {}

    def transact_write_items(self, **kwargs):
        if self.fail_next_transaction:
            self.fail_next_transaction = False
            raise TransactionCanceled()
        staged = copy.deepcopy(self.items)
        touched = set()
        for operation in kwargs["TransactItems"]:
            if "Put" in operation:
                put = operation["Put"]
                item = put["Item"]
                key = self.key(item)
                if key in touched:
                    raise AssertionError("DynamoDB transactions cannot write one item twice")
                touched.add(key)
                old = staged.get(key)
                condition = put.get("ConditionExpression", "")
                if condition == "attribute_not_exists(PK)":
                    if old is not None:
                        raise TransactionCanceled()
                elif "#version = :old_version" in condition:
                    if old is None or old["version"]["N"] != put["ExpressionAttributeValues"][":old_version"]["N"]:
                        raise TransactionCanceled()
                elif condition == "attribute_exists(PK)":
                    if old is None:
                        raise TransactionCanceled()
                elif condition:
                    raise AssertionError("Unknown condition: " + condition)
                staged[key] = copy.deepcopy(item)
            elif "Delete" in operation:
                delete = operation["Delete"]
                key = self.key(delete["Key"])
                if key in touched:
                    raise AssertionError("DynamoDB transactions cannot write one item twice")
                touched.add(key)
                old = staged.get(key)
                condition = delete.get("ConditionExpression", "")
                if condition == "attribute_exists(PK)" and old is None:
                    raise TransactionCanceled()
                if condition not in ("", "attribute_exists(PK)"):
                    raise AssertionError("Unknown condition: " + condition)
                staged.pop(key, None)
            else:
                raise AssertionError("Unexpected DynamoDB transaction operation")
        self.items = staged

    def query(self, **kwargs):
        if kwargs.get("IndexName") == "GSI1":
            items = [
                item for item in self.items.values()
                if item.get("GSI1PK") == {"S": "INCIDENTS"}
            ]
            items.sort(key=lambda item: item["GSI1SK"]["S"], reverse=True)
            start_key = kwargs.get("ExclusiveStartKey")
            if start_key:
                index = next((i for i, item in enumerate(items) if self.key(item) == self.key(start_key)), -1)
                items = items[index + 1:]
            limit = kwargs["Limit"]
            response = {"Items": copy.deepcopy(items[:limit])}
            if len(items) > limit:
                response["LastEvaluatedKey"] = copy.deepcopy(
                    {name: items[limit - 1][name] for name in ("PK", "SK", "GSI1PK", "GSI1SK")}
                )
            return response
        pk = kwargs["ExpressionAttributeValues"][":pk"]["S"]
        values = kwargs["ExpressionAttributeValues"]
        items = [
            item for item in self.items.values()
            if item["PK"]["S"] == pk
            and (
                (":prefix" in values and item["SK"]["S"].startswith(values[":prefix"]["S"]))
                or (":lower" in values and values[":lower"]["S"] <= item["SK"]["S"] <= values[":upper"]["S"])
            )
        ]
        items.sort(key=lambda item: item["SK"]["S"])
        start_key = kwargs.get("ExclusiveStartKey")
        if start_key:
            index = next((i for i, item in enumerate(items) if self.key(item) == self.key(start_key)), -1)
            items = items[index + 1:]
        limit = min(kwargs.get("Limit", len(items)), self.query_page_size or len(items))
        response = {"Items": copy.deepcopy(items[:limit])}
        if len(items) > limit:
            response["LastEvaluatedKey"] = copy.deepcopy({name: items[limit - 1][name] for name in ("PK", "SK")})
        return response


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def transition(event):
    return parse_eventbridge_alarm(event, {ALARM_ARN}, "slash-api", "dev")


class DynamoAdapterTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeDynamoClient()
        self.store = DynamoIncidentStore("test-table", client=self.client)

    def apply(self, name):
        event = fixture(name)
        return self.store.apply(transition(event), event)

    def test_transactional_alarm_insufficient_ok_and_replay(self):
        first = self.apply("alarm.json")
        self.assertEqual(first.outcome, "created")
        self.assertEqual(self.apply("duplicate_alarm.json").outcome, "duplicate")
        self.assertEqual(self.apply("insufficient_data.json").outcome, "updated")
        ok = fixture("ok.json")
        ok["detail"]["previousState"] = {
            "value": "INSUFFICIENT_DATA",
            "timestamp": "2026-10-08T12:05:00.000+0000",
        }
        self.store.apply(transition(ok), ok)
        with self.assertRaises(TransitionConflict):
            self.apply("stale_alarm.json")
        detail = self.store.get_incident(first.incident_id)
        self.assertEqual(detail["alarmState"], "OK")
        self.assertEqual(detail["incidentState"], "NEW")
        self.assertEqual(
            [entry["state"] for entry in detail["stateHistory"]],
            ["ALARM", "INSUFFICIENT_DATA", "OK"],
        )
        self.assertEqual(len(self.store.list_incidents()[0]), 1)

    def test_reversed_delivery_uses_conditional_cursor_without_reopening(self):
        self.client.fail_next_transaction = True
        self.assertEqual(self.apply("ok.json").outcome, "recorded")
        result = self.apply("alarm.json")
        self.assertEqual(result.outcome, "reconstructed")
        detail = self.store.get_incident(result.incident_id)
        self.assertEqual(detail["alarmState"], "OK")
        self.assertEqual([entry["state"] for entry in detail["stateHistory"]], ["ALARM", "OK"])
        self.assertEqual(self.apply("duplicate_alarm.json").outcome, "duplicate")
        self.assertEqual(len(self.store.list_incidents()[0]), 1)

    def test_reversed_three_state_chain_keeps_latest_ok(self):
        ok = fixture("ok.json")
        ok["detail"]["previousState"]["value"] = "INSUFFICIENT_DATA"
        ok["detail"]["previousState"]["timestamp"] = "2026-10-08T12:05:00.000+0000"
        self.store.apply(transition(ok), ok)
        self.apply("insufficient_data.json")
        result = self.apply("alarm.json")
        self.assertEqual(result.outcome, "reconstructed")
        detail = self.store.get_incident(result.incident_id)
        self.assertEqual(detail["alarmState"], "OK")
        self.assertEqual([entry["state"] for entry in detail["stateHistory"]], ["ALARM", "INSUFFICIENT_DATA", "OK"])

    def test_late_ok_splits_dynamo_incidents(self):
        first = self.apply("alarm.json")
        later = fixture("alarm.json")
        later["id"] = "2bfc12fe-5851-4d86-91f8-f71279c99293"
        later["detail"]["state"]["timestamp"] = "2026-10-08T12:20:00.000+0000"
        later["detail"]["previousState"]["timestamp"] = "2026-10-08T12:10:00.456+0000"
        second = self.store.apply(transition(later), later)
        self.assertEqual(second.outcome, "created")
        self.apply("ok.json")
        self.assertEqual(len(self.store.list_incidents()[0]), 2)
        self.assertEqual(self.store.get_incident(first.incident_id)["alarmState"], "OK")
        self.assertEqual(self.store.get_incident(second.incident_id)["alarmState"], "ALARM")

    def test_late_first_alarm_backfills_dynamo_incident(self):
        self.apply("insufficient_data.json")
        later = fixture("alarm.json")
        later["id"] = "5782e180-cff4-49a1-ab16-e614d0ae1686"
        later["detail"]["state"]["timestamp"] = "2026-10-08T12:20:00.000+0000"
        later["detail"]["previousState"]["value"] = "INSUFFICIENT_DATA"
        later["detail"]["previousState"]["timestamp"] = "2026-10-08T12:05:00.000+0000"
        current = self.store.apply(transition(later), later)
        backfill = self.apply("alarm.json")
        self.assertEqual(backfill.outcome, "reconstructed")
        detail = self.store.get_incident(current.incident_id)
        self.assertEqual(detail["incidentId"], backfill.incident_id)
        self.assertEqual(detail["detectedAt"], "2026-10-08T12:00:00.123000Z")
        self.assertEqual([entry["state"] for entry in detail["stateHistory"]], ["ALARM", "INSUFFICIENT_DATA", "ALARM"])
        self.assertEqual(len(self.store.list_incidents()[0]), 1)

    def test_reverse_order_fixture_reprojects_two_episodes_across_query_pages(self):
        self.client.query_page_size = 1
        sequence = fixture("out_of_order_replay.json")
        for name in sequence["fixtureOrder"]:
            self.apply(name)
        incidents, _ = self.store.list_incidents()
        self.assertEqual(len(incidents), 2)
        by_state = {item["alarmState"]: item for item in incidents}
        self.assertEqual(
            [entry["state"] for entry in self.store.get_incident(by_state["OK"]["incidentId"])["stateHistory"]],
            ["ALARM", "OK"],
        )
        self.assertEqual(
            [entry["state"] for entry in self.store.get_incident(by_state["ALARM"]["incidentId"])["stateHistory"]],
            ["ALARM"],
        )

    def test_all_delivery_orders_keep_dynamo_episode_boundaries(self):
        alarm = fixture("alarm.json")
        insufficient = fixture("insufficient_data.json")
        ok = fixture("ok.json")
        ok["detail"]["previousState"] = {
            "value": "INSUFFICIENT_DATA",
            "timestamp": "2026-10-08T12:05:00.000+0000",
        }
        later = fixture("later_alarm.json")
        for order in itertools.permutations((alarm, insufficient, ok, later)):
            with self.subTest(order=tuple(event["id"] for event in order)):
                client = FakeDynamoClient()
                store = DynamoIncidentStore("test-table", client=client)
                for event in order:
                    store.apply(transition(event), event)
                incidents, _ = store.list_incidents()
                self.assertEqual(len(incidents), 2)
                by_state = {item["alarmState"]: item for item in incidents}
                self.assertEqual(by_state["OK"]["alarmOkAt"], "2026-10-08T12:10:00.456000Z")
                self.assertEqual(
                    [entry["state"] for entry in store.get_incident(by_state["OK"]["incidentId"])["stateHistory"]],
                    ["ALARM", "INSUFFICIENT_DATA", "OK"],
                )
                self.assertEqual(by_state["ALARM"]["detectedAt"], "2026-10-08T12:20:00.000000Z")

    def test_invalid_pagination_token_is_rejected(self):
        for encoded in (
            "%%!",
            base64.urlsafe_b64encode(b"null").decode("ascii"),
            base64.urlsafe_b64encode(b"{}").decode("ascii"),
        ):
            with self.subTest(encoded=encoded), self.assertRaises(ValueError):
                self.store.list_incidents(next_token=encoded)


if __name__ == "__main__":
    unittest.main()
