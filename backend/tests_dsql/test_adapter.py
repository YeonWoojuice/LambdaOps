"""Exercise the real adapter with SQL, not an in-memory store.

These tests intentionally delete data, but only in the explicitly opted-in,
local ``lambdaops_adapter_test`` database. They are PostgreSQL compatibility
tests, not evidence of DSQL IAM, ASYNC indexes, OCC codes, or Lambda deployment.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import itertools
import json
import os
from pathlib import Path
import re
import threading
import unittest
from unittest.mock import patch


FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
BASE_TIME = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def timestamp(minute):
    return (BASE_TIME + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def event(state="ALARM", minute=0, previous_state="OK", previous_minute=-1,
          name="adapter-test"):
    value = json.loads((FIXTURES / "alarm.json").read_text())
    value["id"] = "%s-%s-%s" % (name, state, minute)
    value["time"] = timestamp(minute)
    value["detail"]["alarmName"] = name
    value["resources"] = [
        "arn:aws:cloudwatch:ap-northeast-2:123456789012:alarm:" + name
    ]
    value["detail"]["state"].update(value=state, timestamp=timestamp(minute))
    value["detail"]["previousState"] = {
        "value": previous_state, "timestamp": timestamp(previous_minute)
    }
    return value


def api_event(route="GET /incidents", incident_id=None, params=None):
    value = {
        "routeKey": route,
        "requestContext": {"authorizer": {"jwt": {
            "claims": {"sub": "test-operator", "scope": "incidents:read"}
        }}},
    }
    if incident_id is not None:
        value["pathParameters"] = {"incidentId": incident_id}
    if params is not None:
        value["queryStringParameters"] = params
    return value


def local_settings():
    if os.environ.get("LAMBDAOPS_DSQL_TEST") != "1":
        raise unittest.SkipTest("Set LAMBDAOPS_DSQL_TEST=1 for disposable local SQL tests")
    host = os.environ.get("PGHOST", "")
    database = os.environ.get("PGDATABASE", "")
    user = os.environ.get("PGUSER", "")
    port = os.environ.get("PGPORT", "5432")
    if database != "lambdaops_adapter_test":
        raise RuntimeError("Integration tests require PGDATABASE=lambdaops_adapter_test")
    if not (host in {"localhost", "127.0.0.1", "::1"} or host.startswith("/")):
        raise RuntimeError("Integration tests require an explicit local PGHOST")
    if "," in host or not user or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise RuntimeError("Invalid local PostgreSQL connection settings")
    if any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")):
        raise RuntimeError("Unset PGHOSTADDR, PGSERVICE, and PGSERVICEFILE for local tests")
    return {"host": host, "port": int(port), "dbname": database,
            "user": user, "connect_timeout": 5}


class _CursorProxy:
    def __init__(self, cursor, after_execute):
        self.cursor = cursor
        self.after_execute = after_execute

    def execute(self, sql, *args, **kwargs):
        self.cursor.execute(sql, *args, **kwargs)
        self.after_execute(str(sql))
        return self

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def __iter__(self):
        return iter(self.cursor)


class _ConnectionProxy:
    """Inject a failure or concurrent commit at an actual SQL boundary."""

    def __init__(self, connection, after_execute=lambda sql: None, after_commit=None):
        self.connection = connection
        self.after_execute = after_execute
        self.after_commit = after_commit

    def execute(self, sql, *args, **kwargs):
        cursor = self.connection.execute(sql, *args, **kwargs)
        self.after_execute(str(sql))
        return cursor

    def cursor(self, *args, **kwargs):
        return _CursorProxy(self.connection.cursor(*args, **kwargs), self.after_execute)

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        result = self.connection.__exit__(exc_type, exc, traceback)
        if exc_type is None and self.after_commit is not None:
            self.after_commit()
        return result

    def __getattr__(self, name):
        return getattr(self.connection, name)


class DsqlAdapterIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = local_settings()
        import psycopg
        from psycopg.rows import dict_row
        from lambdaops_m3.dsql_store import DsqlIncidentStore
        from lambdaops_m3.query_contract import parse_incident_query
        cls.psycopg = psycopg
        cls.dict_row = staticmethod(dict_row)
        cls.store_type = DsqlIncidentStore
        cls.parse_query = staticmethod(parse_incident_query)

    def connect(self):
        connection = self.psycopg.connect(**self.settings, row_factory=self.dict_row)
        connection.isolation_level = self.psycopg.IsolationLevel.REPEATABLE_READ
        return connection

    def setUp(self):
        self.reset()
        self.store = self.store_type(self.connect)

    def tearDown(self):
        self.reset()

    def reset(self):
        with self.connect() as connection:
            connection.execute("TRUNCATE public.incidents, public.alarm_events, public.alarms")

    def apply(self, raw, service="slash-api", environment="dev", store=None):
        from lambdaops_m3.contracts import parse_eventbridge_alarm
        transition = parse_eventbridge_alarm(raw, {raw["resources"][0]}, service, environment)
        return (store or self.store).apply(transition, raw)

    def page(self, **params):
        return self.store.list_incidents_page(self.parse_query(params))

    def rows(self, sql, params=()):
        with self.connect() as connection:
            return connection.execute(sql, params).fetchall()

    def counts(self):
        return tuple(self.rows("SELECT count(*) AS n FROM public." + table)[0]["n"]
                     for table in ("alarms", "alarm_events", "incidents"))

    def test_transaction_size_guards_rollback_initial_alarm_insert(self):
        from lambdaops_m3.dsql_store import DsqlProjectionTooLarge
        for constant in ("MAX_CHANGED_ROWS", "MAX_TRANSACTION_BYTES"):
            with self.subTest(limit=constant):
                with patch("lambdaops_m3.dsql_store." + constant, 1):
                    with self.assertRaises(DsqlProjectionTooLarge):
                        self.apply(event())
                self.assertEqual(self.counts(), (0, 0, 0))

    def test_missing_ok_does_not_join_disconnected_chains(self):
        first = self.apply(event())
        second = self.apply(event("ALARM", 20, "OK", 10))
        detail = self.store.get_incident(first.incident_id)
        # The next ALARM identifies a missing OK, but it does not identify that
        # OK's predecessor. Close the earlier episode only once that link arrives.
        self.assertEqual(detail["alarmState"], "ALARM")
        self.assertNotIn("alarmOkAt", detail)
        self.assertEqual([row["state"] for row in detail["stateHistory"]], ["ALARM"])
        self.assertNotEqual(first.incident_id, second.incident_id)
        self.apply(event("OK", 10, "ALARM", 0))
        closed = self.store.get_incident(first.incident_id)
        self.assertEqual(closed["alarmState"], "OK")
        self.assertEqual(closed["alarmOkAt"], timestamp(10))
        self.assertEqual(self.store.get_incident(second.incident_id)["alarmState"], "ALARM")

    def test_alarm_duplicate_preserves_first_evidence_and_classification(self):
        raw = event()
        first = self.apply(raw)
        duplicate = deepcopy(raw)
        duplicate["id"] = "different-eventbridge-delivery"
        duplicate["detail"]["state"]["reason"] = "Different ancillary text"
        repeated = self.apply(duplicate, service="changed-service", environment="prod")
        self.assertEqual(repeated.outcome, "duplicate")
        self.assertEqual(self.counts(), (1, 1, 1))
        detail = self.store.get_incident(first.incident_id)
        self.assertRegex(detail["incidentId"], r"^inc_[0-9a-f]{32}$")
        self.assertEqual(detail["service"], "slash-api")
        self.assertEqual(detail["environment"], "dev")
        self.assertEqual(detail["stateHistory"][0]["rawEvent"], raw)
        self.assertEqual(self.rows("SELECT revision FROM public.alarms")[0]["revision"], 1)

    def test_same_dedupe_with_conflicting_predecessor_is_not_successful_duplicate(self):
        from lambdaops_m3.store import TransitionConflict
        raw = event()
        self.apply(raw)
        contradictory = deepcopy(raw)
        contradictory["detail"]["previousState"]["value"] = "INSUFFICIENT_DATA"
        with self.assertRaises(TransitionConflict):
            self.apply(contradictory)
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(self.page()["items"][0]["alarmState"], "ALARM")

    def test_same_timestamp_different_state_rolls_back(self):
        from lambdaops_m3.store import TransitionConflict
        self.apply(event())
        with self.assertRaises(TransitionConflict):
            self.apply(event("OK"))
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_insufficient_data_keeps_episode_open_ok_only_closes_alarm(self):
        first = self.apply(event())
        self.apply(event("INSUFFICIENT_DATA", 5, "ALARM", 0))
        detail = self.store.get_incident(first.incident_id)
        self.assertEqual(detail["alarmState"], "INSUFFICIENT_DATA")
        self.assertNotIn("alarmOkAt", detail)
        self.apply(event("OK", 10, "INSUFFICIENT_DATA", 5))
        detail = self.store.get_incident(first.incident_id)
        self.assertEqual(detail["alarmState"], "OK")
        self.assertEqual(detail["alarmOkAt"], timestamp(10))
        self.assertEqual(detail["incidentState"], "NEW")

    def test_late_start_and_anchor_correction_preserve_id_and_created_time(self):
        self.apply(event("INSUFFICIENT_DATA", 5, "ALARM", 0))
        current = self.apply(event("ALARM", 10, "INSUFFICIENT_DATA", 5))
        before = self.store.get_incident(current.incident_id)
        self.apply(event())
        after = self.store.get_incident(current.incident_id)
        self.assertEqual(after["incidentId"], current.incident_id)
        self.assertEqual(after["createdAt"], before["createdAt"])
        self.assertEqual(after["detectedAt"], timestamp(0))
        self.assertEqual(after["originalEventRef"], event()["id"])
        self.assertEqual(self.page()["totalItems"], 1)

    def test_bridge_keeps_oldest_created_record_even_when_anchor_owned_by_loser(self):
        older_record = self.apply(event("ALARM", 10, "INSUFFICIENT_DATA", 5))
        later_record = self.apply(event())
        self.assertEqual(self.page()["totalItems"], 2)
        self.apply(event("INSUFFICIENT_DATA", 5, "ALARM", 0))
        result = self.page()
        self.assertEqual(result["totalItems"], 1)
        self.assertEqual(result["items"][0]["incidentId"], older_record.incident_id)
        detail = self.store.get_incident(later_record.incident_id)
        self.assertEqual(detail["incidentId"], older_record.incident_id)
        self.assertEqual(detail["requestedIncidentId"], later_record.incident_id)
        self.assertTrue(detail["redirected"])
        self.assertEqual(detail["originalEventRef"], event()["id"])
        self.assertEqual([item["state"] for item in detail["stateHistory"]],
                         ["ALARM", "INSUFFICIENT_DATA", "ALARM"])

    def test_second_merge_flattens_previous_redirects(self):
        first = self.apply(event())
        middle = self.apply(event("ALARM", 5, "INSUFFICIENT_DATA", 3))
        last = self.apply(event("ALARM", 10, "INSUFFICIENT_DATA", 8))
        self.apply(event("INSUFFICIENT_DATA", 8, "ALARM", 5))
        self.apply(event("INSUFFICIENT_DATA", 3, "ALARM", 0))
        rows = self.rows("SELECT incident_id, redirect_to_incident_id FROM public.incidents")
        redirects = {row["incident_id"]: row["redirect_to_incident_id"] for row in rows}
        self.assertEqual(redirects, {first.incident_id: None,
                                    middle.incident_id: first.incident_id,
                                    last.incident_id: first.incident_id})
        self.assertEqual(self.page()["totalItems"], 1)

    def test_representative_tie_breaks_by_incident_id(self):
        first = self.apply(event())
        second = self.apply(event("ALARM", 10, "INSUFFICIENT_DATA", 5))
        with self.connect() as connection:
            connection.execute("UPDATE public.incidents SET created_at = %s", (timestamp(-20),))
        self.apply(event("INSUFFICIENT_DATA", 5, "ALARM", 0))
        self.assertEqual(self.page()["items"][0]["incidentId"],
                         min(first.incident_id, second.incident_id))

    def test_all_orders_converge_to_two_episodes_and_preserve_latest_alarm_state(self):
        values = [event(), event("INSUFFICIENT_DATA", 5, "ALARM", 0),
                  event("OK", 10, "INSUFFICIENT_DATA", 5), event("ALARM", 20, "OK", 10)]
        for order in itertools.permutations(values):
            with self.subTest(order=[value["id"] for value in order]):
                self.reset()
                for raw in order:
                    self.apply(raw)
                page = self.page(sortOrder="asc")
                self.assertEqual(page["totalItems"], 2)
                self.assertEqual([row["alarmState"] for row in page["items"]], ["OK", "ALARM"])
                self.assertEqual(self.rows("SELECT latest_state FROM public.alarms")[0]["latest_state"], "ALARM")
                first, second = [self.store.get_incident(row["incidentId"]) for row in page["items"]]
                self.assertEqual([item["state"] for item in first["stateHistory"]],
                                 ["ALARM", "INSUFFICIENT_DATA", "OK"])
                self.assertEqual([item["state"] for item in second["stateHistory"]], ["ALARM"])

    def test_exception_after_event_insert_leaves_no_partial_rows(self):
        injected = []
        def fail_after_insert(sql):
            if re.search(r"INSERT\s+INTO\s+public\.alarm_events", sql, re.I):
                injected.append(True)
                raise RuntimeError("Injected storage failure")
        broken = self.store_type(lambda: _ConnectionProxy(self.connect(), fail_after_insert))
        with self.assertRaisesRegex(RuntimeError, "Injected storage failure"):
            self.apply(event(), store=broken)
        self.assertTrue(injected)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_lost_commit_response_is_safe_to_redeliver(self):
        def lose_response():
            raise ConnectionError("Commit response lost after successful commit")
        uncertain = self.store_type(lambda: _ConnectionProxy(self.connect(), after_commit=lose_response))
        with self.assertRaises(ConnectionError):
            self.apply(event(), store=uncertain)
        before = self.page()["items"][0]["incidentId"]
        self.assertEqual(self.apply(event()).outcome, "duplicate")
        self.assertEqual(self.page()["items"][0]["incidentId"], before)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_concurrent_duplicate_deliveries_commit_one_incident(self):
        barrier = threading.Barrier(2)
        def deliver():
            barrier.wait(timeout=10)
            return self.apply(event())
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(deliver) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(result.outcome for result in results), ["created", "duplicate"])
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_concurrent_new_transitions_recompute_without_lost_updates(self):
        first = self.apply(event())
        barrier = threading.Barrier(2)
        def deliver(raw):
            barrier.wait(timeout=10)
            return self.apply(raw)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(deliver, raw) for raw in
                       [event("INSUFFICIENT_DATA", 5, "ALARM", 0),
                        event("OK", 10, "INSUFFICIENT_DATA", 5)]]
            for future in futures:
                future.result(timeout=20)
        detail = self.store.get_incident(first.incident_id)
        self.assertEqual(detail["alarmState"], "OK")
        self.assertEqual(len(detail["stateHistory"]), 3)
        self.assertEqual(self.rows("SELECT revision FROM public.alarms")[0]["revision"], 3)

    def seed_list(self):
        self.apply(event(name="Alpha-api"), service="billing", environment="dev")
        self.apply(event("OK", 2, "ALARM", 0, name="Alpha-api"), service="billing", environment="dev")
        self.apply(event(minute=5, name="Beta-db"), service="billing", environment="prod")
        self.apply(event(minute=10, name="Gamma-worker"), service="orders", environment="prod")

    def test_filters_search_dates_and_count_use_identical_conditions(self):
        self.seed_list()
        response = self.page(service="billing", environment="dev", alarmState="OK",
                             incidentState="NEW", search="ALPHA", **{"from": timestamp(0), "to": timestamp(5)})
        self.assertEqual(response["totalItems"], 1)
        self.assertEqual(response["items"][0]["alarmName"], "Alpha-api")
        self.assertEqual(self.page(**{"from": timestamp(5), "to": timestamp(10)})["totalItems"], 1)
        self.assertEqual(self.page(service="Billing")["totalItems"], 0)
        self.assertNotIn("rawEvent", response["items"][0])
        self.assertNotIn("stateHistory", response["items"][0])

    def test_search_treats_percent_underscore_and_backslash_as_literal(self):
        for name in ("literal%rate", "literal_rate", "literal\\rate", "literalXrate"):
            self.apply(event(name=name))
        for query, expected in (("literal%", "literal%rate"), ("literal_", "literal_rate"),
                                ("literal\\", "literal\\rate")):
            with self.subTest(query=query):
                page = self.page(search=query)
                self.assertEqual(page["totalItems"], 1)
                self.assertEqual(page["items"][0]["alarmName"], expected)
        self.assertEqual(self.page(search="' OR 1=1 --")["totalItems"], 0)

    def test_numbered_pages_empty_page_and_exact_total(self):
        self.seed_list()
        first = self.page(page="1", pageSize="2")
        second = self.page(page="2", pageSize="2")
        outside = self.page(page="3", pageSize="2")
        self.assertEqual((first["totalItems"], first["totalPages"], first["hasNextPage"]), (3, 2, True))
        self.assertEqual((len(second["items"]), second["hasNextPage"]), (1, False))
        self.assertEqual((outside["items"], outside["totalItems"], outside["totalPages"]), ([], 3, 2))
        ids = [row["incidentId"] for row in first["items"] + second["items"]]
        self.assertEqual(len(set(ids)), 3)

    def test_sort_changes_and_ties_are_stable(self):
        self.apply(event(name="tie-a"))
        self.apply(event(name="tie-b"))
        asc = self.page(sortOrder="asc")["items"]
        desc = self.page(sortOrder="desc")["items"]
        self.assertEqual([row["incidentId"] for row in asc],
                         sorted(row["incidentId"] for row in asc))
        self.assertEqual([row["incidentId"] for row in desc],
                         list(reversed([row["incidentId"] for row in asc])))
        self.apply(event("OK", 10, "ALARM", 0, name="tie-a"))
        latest = self.page(sortBy="lastStateTransitionAt", sortOrder="desc")["items"]
        self.assertEqual(latest[0]["alarmName"], "tie-a")

    def test_count_and_items_share_snapshot_despite_concurrent_insert(self):
        self.apply(event(name="first"))
        inserted = []
        def commit_between_queries(sql):
            if re.search(r"COUNT\s*\(", sql, re.I) and not inserted:
                inserted.append(True)
                self.apply(event(minute=5, name="concurrent"))
        reader = self.store_type(lambda: _ConnectionProxy(self.connect(), commit_between_queries))
        response = reader.list_incidents_page(self.parse_query({}))
        self.assertTrue(inserted, "The hook must observe the real count query")
        self.assertEqual(response["totalItems"], 1)
        self.assertEqual([row["alarmName"] for row in response["items"]], ["first"])
        self.assertEqual(self.page()["totalItems"], 2)

    def test_detail_snapshot_preserves_summary_and_history_together(self):
        first = self.apply(event())
        updated = []
        def append_after_summary(sql):
            if re.search(r"FROM\s+public\.incidents", sql, re.I) and not updated:
                updated.append(True)
                self.apply(event("OK", 10, "ALARM", 0))
        reader = self.store_type(lambda: _ConnectionProxy(self.connect(), append_after_summary))
        detail = reader.get_incident(first.incident_id)
        self.assertTrue(updated)
        self.assertEqual(detail["alarmState"], "ALARM")
        self.assertEqual([item["state"] for item in detail["stateHistory"]], ["ALARM"])
        self.assertEqual(self.store.get_incident(first.incident_id)["alarmState"], "OK")

    def test_http_auth_numbered_list_detail_and_validation(self):
        from lambdaops_m3.read_handler import handle_request
        first = self.apply(event())
        listing = handle_request(api_event(params={"page": "1", "pageSize": "1"}), self.store)
        self.assertEqual(listing["statusCode"], 200)
        self.assertEqual(json.loads(listing["body"])["totalItems"], 1)
        request = api_event("GET /incidents/{incidentId}", first.incident_id)
        self.assertEqual(handle_request(request, self.store)["statusCode"], 200)
        request.pop("requestContext")
        request["headers"] = {"authorization": "Bearer forged"}
        self.assertEqual(handle_request(request, self.store)["statusCode"], 401)
        request = api_event()
        request["requestContext"]["authorizer"]["jwt"]["claims"]["scope"] = "profile:read"
        self.assertEqual(handle_request(request, self.store)["statusCode"], 403)
        self.assertEqual(handle_request(api_event(params={"limit": "10"}), self.store)["statusCode"], 400)
        self.assertEqual(handle_request(api_event("GET /incidents/{incidentId}", "inc_" + "0" * 32), self.store)["statusCode"], 404)

    def test_sqs_batch_reports_only_conflicting_record(self):
        from lambdaops_m3.ingest_handler import process_batch
        values = [event(), event("OK"), event("OK", 10, "ALARM", 0)]
        batch = {"Records": [{"messageId": str(index), "eventSource": "aws:sqs",
                              "body": json.dumps(raw)} for index, raw in enumerate(values)]}
        with self.assertLogs("lambdaops_m3.ingest_handler", level="ERROR"):
            response = process_batch(batch, self.store, {values[0]["resources"][0]}, "slash-api", "dev")
        self.assertEqual(response, {"batchItemFailures": [{"itemIdentifier": "1"}]})
        self.assertEqual(self.counts(), (1, 2, 1))
        self.assertEqual(self.page()["items"][0]["alarmState"], "OK")


if __name__ == "__main__":
    unittest.main()
