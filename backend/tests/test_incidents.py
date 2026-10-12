import copy
import json
from pathlib import Path
import unittest

from lambdaops_m3.contracts import parse_eventbridge_alarm
from lambdaops_m3.ingest_handler import process_batch
from lambdaops_m3.read_handler import handle_request
from lambdaops_m3.store import MemoryIncidentStore, TransitionConflict


FIXTURES = Path(__file__).parent / "fixtures"
ALARM_ARN = (
    "arn:aws:cloudwatch:ap-northeast-2:123456789012:alarm:"
    "lambdaops-dev-slash-api-api-5xx-rate"
)


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def transition(event):
    return parse_eventbridge_alarm(event, {ALARM_ARN}, "slash-api", "dev")


def sqs_record(message_id, event):
    return {"messageId": message_id, "eventSource": "aws:sqs", "body": json.dumps(event)}


def api_event(route, incident_id=None, scope="incidents:read"):
    event = {
        "routeKey": route,
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "test-user", "scope": scope}}}},
    }
    if incident_id is not None:
        event["pathParameters"] = {"incidentId": incident_id}
    return event


class IncidentEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryIncidentStore()

    def apply(self, name):
        event = fixture(name)
        return self.store.apply(transition(event), event)

    def test_alarm_creates_one_incident_and_duplicate_is_idempotent(self):
        first = self.apply("alarm.json")
        duplicate = self.apply("duplicate_alarm.json")
        self.assertEqual(first.outcome, "created")
        self.assertEqual(duplicate.outcome, "duplicate")
        self.assertEqual(self.store.list_incidents()[0][0]["incidentId"], first.incident_id)
        self.assertEqual(len(self.store.list_incidents()[0]), 1)
        detail = self.store.get_incident(first.incident_id)
        self.assertEqual(detail["alarmState"], "ALARM")
        self.assertEqual(detail["incidentState"], "NEW")
        self.assertEqual(detail["originalEventRef"], fixture("alarm.json")["id"])
        self.assertEqual(len(detail["stateHistory"]), 1)

    def test_insufficient_data_keeps_episode_open_until_ok(self):
        first = self.apply("alarm.json")
        self.assertEqual(self.apply("insufficient_data.json").outcome, "updated")
        detail = self.store.get_incident(first.incident_id)
        self.assertEqual(detail["alarmState"], "INSUFFICIENT_DATA")
        self.assertNotIn("alarmOkAt", detail)
        self.assertEqual(detail["incidentState"], "NEW")
        self.assertEqual(len(detail["stateHistory"]), 2)

        # A return to ALARM after insufficient data is still the same episode.
        returned = fixture("alarm.json")
        returned["id"] = "d9f49c80-362d-4e6d-a11f-41614332cb45"
        returned["detail"]["state"]["timestamp"] = "2026-10-08T12:07:00.000+0000"
        returned["detail"]["previousState"] = {
            "value": "INSUFFICIENT_DATA",
            "timestamp": "2026-10-08T12:05:00.000+0000",
        }
        self.assertEqual(self.store.apply(transition(returned), returned).outcome, "updated")
        self.assertEqual(len(self.store.list_incidents()[0]), 1)
        self.assertEqual(self.store.get_incident(first.incident_id)["alarmState"], "ALARM")

    def test_ok_records_alarm_end_without_resolving_incident(self):
        first = self.apply("alarm.json")
        self.apply("ok.json")
        detail = self.store.get_incident(first.incident_id)
        self.assertEqual(detail["alarmState"], "OK")
        self.assertEqual(detail["alarmOkAt"], "2026-10-08T12:10:00.456000Z")
        self.assertEqual(detail["incidentState"], "NEW")
        self.assertEqual([item["state"] for item in detail["stateHistory"]], ["ALARM", "OK"])
        self.assertEqual(detail["stateHistory"][0]["rawEvent"]["id"], fixture("alarm.json")["id"])

    def test_old_alarm_does_not_reopen_or_overwrite_ok(self):
        first = self.apply("alarm.json")
        self.apply("ok.json")
        with self.assertRaises(TransitionConflict):
            self.apply("stale_alarm.json")
        self.assertEqual(len(self.store.list_incidents()[0]), 1)
        self.assertEqual(self.store.get_incident(first.incident_id)["alarmState"], "OK")

    def test_new_alarm_after_ok_is_a_new_episode(self):
        first = self.apply("alarm.json")
        self.apply("ok.json")
        later = fixture("alarm.json")
        later["id"] = "4fced819-a7fc-4403-af5d-97742a9818e3"
        later["detail"]["state"]["timestamp"] = "2026-10-08T12:20:00.000+0000"
        later["detail"]["previousState"]["timestamp"] = "2026-10-08T12:10:00.456+0000"
        second = self.store.apply(transition(later), later)
        self.assertEqual(second.outcome, "created")
        self.assertNotEqual(first.incident_id, second.incident_id)
        self.assertEqual(len(self.store.list_incidents()[0]), 2)
        self.assertEqual(self.store.get_incident(first.incident_id)["alarmState"], "OK")
        self.assertEqual(self.store.get_incident(second.incident_id)["alarmState"], "ALARM")
        # A delayed old OK cannot close the newer episode.
        self.assertEqual(self.apply("ok.json").outcome, "duplicate")
        self.assertEqual(self.store.get_incident(second.incident_id)["alarmState"], "ALARM")

    def test_ok_before_alarm_arrival_reconstructs_closed_episode(self):
        self.assertEqual(self.apply("ok.json").outcome, "recorded")
        result = self.apply("alarm.json")
        self.assertEqual(result.outcome, "reconstructed")
        detail = self.store.get_incident(result.incident_id)
        self.assertEqual(detail["alarmState"], "OK")
        self.assertEqual(detail["alarmOkAt"], "2026-10-08T12:10:00.456000Z")
        self.assertEqual([entry["state"] for entry in detail["stateHistory"]], ["ALARM", "OK"])
        self.assertEqual(self.apply("alarm.json").outcome, "duplicate")
        self.assertEqual(len(self.store.list_incidents()[0]), 1)

    def test_insufficient_data_before_alarm_reconstructs_open_episode(self):
        self.assertEqual(self.apply("insufficient_data.json").outcome, "recorded")
        result = self.apply("alarm.json")
        self.assertEqual(result.outcome, "reconstructed")
        detail = self.store.get_incident(result.incident_id)
        self.assertEqual(detail["alarmState"], "INSUFFICIENT_DATA")
        self.assertNotIn("alarmOkAt", detail)
        self.assertEqual([entry["state"] for entry in detail["stateHistory"]], ["ALARM", "INSUFFICIENT_DATA"])
        ok = fixture("ok.json")
        ok["detail"]["previousState"] = {
            "value": "INSUFFICIENT_DATA",
            "timestamp": "2026-10-08T12:05:00.000+0000",
        }
        self.store.apply(transition(ok), ok)
        self.assertEqual(self.store.get_incident(result.incident_id)["alarmState"], "OK")

    def test_older_unlinked_alarm_cannot_overwrite_ok(self):
        self.assertEqual(self.apply("ok.json").outcome, "recorded")
        with self.assertRaises(TransitionConflict):
            self.apply("stale_alarm.json")
        self.assertEqual(self.store.list_incidents()[0], [])

    def test_insufficient_then_ok_before_alarm_reconstructs_closed_episode(self):
        self.apply("insufficient_data.json")
        ok = fixture("ok.json")
        ok["detail"]["previousState"]["value"] = "INSUFFICIENT_DATA"
        ok["detail"]["previousState"]["timestamp"] = "2026-10-08T12:05:00.000+0000"
        self.store.apply(transition(ok), ok)
        result = self.apply("alarm.json")
        self.assertEqual(result.outcome, "reconstructed")
        self.assertEqual(self.store.get_incident(result.incident_id)["alarmState"], "OK")
        self.assertEqual(
            [entry["state"] for entry in self.store.get_incident(result.incident_id)["stateHistory"]],
            ["ALARM", "INSUFFICIENT_DATA", "OK"],
        )

    def test_fully_reversed_ok_insufficient_alarm_chain(self):
        ok = fixture("ok.json")
        ok["detail"]["previousState"]["value"] = "INSUFFICIENT_DATA"
        ok["detail"]["previousState"]["timestamp"] = "2026-10-08T12:05:00.000+0000"
        self.assertEqual(self.store.apply(transition(ok), ok).outcome, "recorded")
        self.apply("insufficient_data.json")
        result = self.apply("alarm.json")
        self.assertEqual(result.outcome, "reconstructed")
        detail = self.store.get_incident(result.incident_id)
        self.assertEqual(detail["alarmState"], "OK")
        self.assertEqual(
            [entry["state"] for entry in detail["stateHistory"]],
            ["ALARM", "INSUFFICIENT_DATA", "OK"],
        )

    def test_reversed_chain_stops_at_prior_ok_boundary(self):
        latest_ok = fixture("ok.json")
        latest_ok["detail"]["previousState"]["value"] = "INSUFFICIENT_DATA"
        latest_ok["detail"]["previousState"]["timestamp"] = "2026-10-08T12:05:00.000+0000"
        self.store.apply(transition(latest_ok), latest_ok)
        insufficient = fixture("insufficient_data.json")
        insufficient["detail"]["previousState"]["value"] = "OK"
        insufficient["detail"]["previousState"]["timestamp"] = "2026-10-08T12:00:00.000+0000"
        self.store.apply(transition(insufficient), insufficient)

        prior_ok = fixture("ok.json")
        prior_ok["id"] = "61bd6183-738f-490c-a226-c879d2cb7a69"
        prior_ok["detail"]["state"]["timestamp"] = "2026-10-08T12:00:00.000+0000"
        prior_ok["detail"]["previousState"]["timestamp"] = "2026-10-08T11:50:00.000+0000"
        self.store.apply(transition(prior_ok), prior_ok)

        prior_alarm = fixture("alarm.json")
        prior_alarm["id"] = "fb8ab84b-ae4d-4f3e-9370-6c08b24cbda8"
        prior_alarm["detail"]["state"]["timestamp"] = "2026-10-08T11:50:00.000+0000"
        prior_alarm["detail"]["previousState"]["timestamp"] = "2026-10-08T11:40:00.000+0000"
        result = self.store.apply(transition(prior_alarm), prior_alarm)
        detail = self.store.get_incident(result.incident_id)
        self.assertEqual(result.outcome, "reconstructed")
        self.assertEqual(detail["alarmOkAt"], "2026-10-08T12:00:00.000000Z")
        self.assertEqual([entry["state"] for entry in detail["stateHistory"]], ["ALARM", "OK"])

    def test_late_closed_episode_does_not_change_newer_active_episode(self):
        self.apply("ok.json")
        later = fixture("alarm.json")
        later["id"] = "74e764c5-4775-40fd-adfb-f084e93ef673"
        later["detail"]["state"]["timestamp"] = "2026-10-08T12:20:00.000+0000"
        later["detail"]["previousState"]["timestamp"] = "2026-10-08T12:10:00.456+0000"
        newer = self.store.apply(transition(later), later)
        older = self.apply("alarm.json")
        self.assertEqual(older.outcome, "reconstructed")
        self.assertEqual(self.store.get_incident(older.incident_id)["alarmState"], "OK")
        self.assertEqual(self.store.get_incident(newer.incident_id)["alarmState"], "ALARM")
        self.assertEqual(len(self.store.list_incidents()[0]), 2)

    def test_late_ok_still_splits_two_alarm_episodes(self):
        first = self.apply("alarm.json")
        later = fixture("alarm.json")
        later["id"] = "f27ed4d5-e722-4c13-832b-ad3e6562ba26"
        later["detail"]["state"]["timestamp"] = "2026-10-08T12:20:00.000+0000"
        later["detail"]["previousState"]["timestamp"] = "2026-10-08T12:10:00.456+0000"
        second = self.store.apply(transition(later), later)
        self.assertEqual(second.outcome, "created")
        self.assertNotEqual(first.incident_id, second.incident_id)
        self.apply("ok.json")
        self.assertEqual(len(self.store.list_incidents()[0]), 2)
        self.assertEqual(self.store.get_incident(first.incident_id)["alarmState"], "OK")
        self.assertEqual(self.store.get_incident(first.incident_id)["alarmOkAt"], "2026-10-08T12:10:00.456000Z")
        self.assertEqual(self.store.get_incident(second.incident_id)["alarmState"], "ALARM")
        self.assertEqual([entry["state"] for entry in self.store.get_incident(first.incident_id)["stateHistory"]], ["ALARM", "OK"])

    def test_late_first_alarm_backfills_active_episode_start(self):
        self.apply("insufficient_data.json")
        later = fixture("alarm.json")
        later["id"] = "ca808190-834b-4caa-b23e-7b942715f674"
        later["detail"]["state"]["timestamp"] = "2026-10-08T12:20:00.000+0000"
        later["detail"]["previousState"]["value"] = "INSUFFICIENT_DATA"
        later["detail"]["previousState"]["timestamp"] = "2026-10-08T12:05:00.000+0000"
        current = self.store.apply(transition(later), later)
        self.assertEqual(current.outcome, "created")
        backfill = self.apply("alarm.json")
        self.assertEqual(backfill.outcome, "reconstructed")
        detail = self.store.get_incident(current.incident_id)
        self.assertEqual(detail["incidentId"], backfill.incident_id)
        self.assertEqual(detail["detectedAt"], "2026-10-08T12:00:00.123000Z")
        self.assertEqual(detail["alarmState"], "ALARM")
        self.assertEqual([entry["state"] for entry in detail["stateHistory"]], ["ALARM", "INSUFFICIENT_DATA", "ALARM"])
        self.assertEqual(len(self.store.list_incidents()[0]), 1)

    def test_same_timestamp_different_state_is_rejected(self):
        alarm = fixture("alarm.json")
        self.store.apply(transition(alarm), alarm)
        conflicting = copy.deepcopy(alarm)
        conflicting["id"] = "c50d0109-fd3d-4fed-bec9-05da55f5940e"
        conflicting["detail"]["state"]["value"] = "OK"
        with self.assertRaises(TransitionConflict):
            self.store.apply(transition(conflicting), conflicting)


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryIncidentStore()

    def test_sqs_partial_failure_only_retries_bad_record(self):
        bad = fixture("alarm.json")
        bad["detail-type"] = "CloudWatch Alarm Configuration Change"
        batch = {"Records": [
            sqs_record("good-alarm", fixture("alarm.json")),
            sqs_record("bad", bad),
            sqs_record("good-ok", fixture("ok.json")),
        ]}
        with self.assertLogs("lambdaops_m3.ingest_handler", level="ERROR"):
            response = process_batch(batch, self.store, {ALARM_ARN}, "slash-api", "dev")
        self.assertEqual(response, {"batchItemFailures": [{"itemIdentifier": "bad"}]})
        incident = self.store.list_incidents()[0][0]
        self.assertEqual(self.store.get_incident(incident["incidentId"])["alarmState"], "OK")

    def test_replayed_sqs_message_does_not_duplicate(self):
        batch = {"Records": [sqs_record("m1", fixture("alarm.json"))]}
        for _ in range(2):
            self.assertEqual(
                process_batch(batch, self.store, {ALARM_ARN}, "slash-api", "dev"),
                {"batchItemFailures": []},
            )
        self.assertEqual(len(self.store.list_incidents()[0]), 1)

    def test_detail_requires_validated_jwt_context_and_read_scope(self):
        alarm = fixture("alarm.json")
        incident_id = self.store.apply(transition(alarm), alarm).incident_id
        request = api_event("GET /incidents/{incidentId}", incident_id)
        self.assertEqual(handle_request(request, self.store)["statusCode"], 200)

        no_auth = copy.deepcopy(request)
        no_auth.pop("requestContext")
        no_auth["headers"] = {"authorization": "Bearer forged-token"}
        self.assertEqual(handle_request(no_auth, self.store)["statusCode"], 401)
        no_permission = api_event("GET /incidents/{incidentId}", incident_id, "profile:read")
        self.assertEqual(handle_request(no_permission, self.store)["statusCode"], 403)
        self.assertEqual(handle_request(api_event("GET /incidents"), self.store)["statusCode"], 200)

        client_token = api_event("GET /incidents/{incidentId}", incident_id, "")
        client_token["requestContext"]["authorizer"]["jwt"] = {
            "claims": {"client_id": "machine-client", "scp": ["incidents:read"]},
            "scopes": ["incidents:read"],
        }
        self.assertEqual(handle_request(client_token, self.store)["statusCode"], 200)

    def test_cognito_custom_scope_can_authorize_detail(self):
        alarm = fixture("alarm.json")
        incident_id = self.store.apply(transition(alarm), alarm).incident_id
        access_token_event = api_event(
            "GET /incidents/{incidentId}",
            incident_id,
            "openid email lambdaops/incidents.read",
        )

        self.assertEqual(
            handle_request(access_token_event, self.store, required_scope="lambdaops/incidents.read")["statusCode"],
            200,
        )
        self.assertEqual(
            handle_request(access_token_event, self.store, required_scope="incidents:read")["statusCode"],
            403,
        )

    def test_detail_not_found_and_bad_id(self):
        self.assertEqual(
            handle_request(api_event("GET /incidents/{incidentId}", "inc_" + "0" * 32), self.store)["statusCode"],
            404,
        )
        self.assertEqual(
            handle_request(api_event("GET /incidents/{incidentId}", "../invalid"), self.store)["statusCode"],
            400,
        )


if __name__ == "__main__":
    unittest.main()
