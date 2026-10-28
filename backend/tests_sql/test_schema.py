"""Check actual DML constraints on an already initialized, disposable PostgreSQL.

Run explicitly from backend with PGHOST, PGPORT, PGDATABASE and PGUSER set:
    python3 -m unittest discover -s tests_sql -v

The target must be local and all three tables must initially be empty. This
suite does not create/drop schema or connect to AWS. Every case uses its own
BEGIN/ROLLBACK session. With ON_ERROR_STOP, a rejected statement ends psql;
closing that connection rolls back its open transaction as well.

Passing these checks establishes local PostgreSQL constraint behavior, not
Aurora DSQL syntax support, concurrency, IAM, or application projection rules.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from uuid import UUID


ACCOUNT = "123456789012"
REGION = "ap-northeast-2"
START = "2026-10-28T12:00:00.123456Z"
PREVIOUS = "2026-10-28T11:59:00.000000Z"
LATER = "2026-10-28T12:05:00.000000Z"
END = "2026-10-28T12:10:00.000000Z"


def digest(parts):
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def alarm(name="schema-test-a"):
    arn = f"arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:{name}"
    return {
        "alarm_id": "ALARM#" + digest([ACCOUNT, REGION, arn]),
        "account_id": ACCOUNT,
        "region": REGION,
        "alarm_arn": arn,
        "alarm_name": name,
    }


def event(parent=None, state="ALARM", at=START, previous_state="OK", previous_at=PREVIOUS):
    parent = parent or alarm()
    event_id = "event-reference-not-required-to-be-a-uuid"
    raw = {
        "id": event_id,
        "source": "aws.cloudwatch",
        "detail-type": "CloudWatch Alarm State Change",
        "account": ACCOUNT,
        "region": REGION,
        "resources": [parent["alarm_arn"]],
        "detail": {
            "alarmName": parent["alarm_name"],
            "state": {"value": state, "timestamp": at},
            "previousState": {"value": previous_state, "timestamp": previous_at},
        },
    }
    return {
        "dedupe_key": digest([ACCOUNT, REGION, parent["alarm_arn"], state, at]),
        "alarm_id": parent["alarm_id"],
        "eventbridge_event_id": event_id,
        "state": state,
        "transitioned_at": at,
        "previous_state": previous_state,
        "previous_transitioned_at": previous_at,
        "service": "slash-api",
        "environment": "dev",
        "raw_event": json.dumps(raw),
    }


def incident_id(number=1):
    return "inc_" + UUID(int=number, version=4).hex


def incident(anchor=None, number=1):
    anchor = anchor or event()
    return {
        "incident_id": incident_id(number),
        "alarm_id": anchor["alarm_id"],
        "anchor_event_key": anchor["dedupe_key"],
        "service": "slash-api",
        "environment": "dev",
        "detected_at": anchor["transitioned_at"],
        "alarm_state": "ALARM",
        "last_state_transition_at": anchor["transitioned_at"],
    }


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + value.replace("'", "''") + "'"


def insert(table, values):
    columns = ", ".join(values)
    data = ", ".join(literal(value) for value in values.values())
    return f"INSERT INTO public.{table} ({columns}) VALUES ({data});\n"


def setup_event(parent=None, transition=None):
    parent = parent or alarm()
    transition = transition or event(parent)
    return insert("alarms", parent) + insert("alarm_events", transition)


def setup_incident():
    return setup_event() + insert("incidents", incident())


def two_incidents():
    later = event(at=LATER, previous_state="INSUFFICIENT_DATA", previous_at="2026-10-28T12:03:00.000000Z")
    return (
        setup_event()
        + insert("incidents", incident(number=2))
        + insert("alarm_events", later)
        + insert("incidents", incident(later, number=1))
    )


class SchemaConstraintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER")
        if any(not os.environ.get(name) for name in required):
            raise RuntimeError("Set PGHOST, PGPORT, PGDATABASE and PGUSER for an empty disposable local database.")
        host = os.environ["PGHOST"]
        if "," in host or (host not in ("localhost", "127.0.0.1", "::1") and not Path(host).is_absolute()):
            raise RuntimeError("SQL constraint tests accept only a local host or an absolute Unix socket directory.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$-]*", os.environ["PGDATABASE"]):
            raise RuntimeError("PGDATABASE must be a simple local database name, not a connection string or URI.")
        cls.psql = shutil.which("psql")
        if cls.psql is None:
            raise RuntimeError("Install psql and put it on PATH before running tests_sql.")
        cls.connection_env = dict(os.environ, PGCONNECT_TIMEOUT="5", PGAPPNAME="lambdaops-schema-tests", LC_ALL="C")
        for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
            cls.connection_env.pop(name, None)
        cls.connection_arguments = [
            "-h", cls.connection_env["PGHOST"],
            "-p", cls.connection_env["PGPORT"],
            "-U", cls.connection_env["PGUSER"],
            "-d", cls.connection_env["PGDATABASE"],
        ]
        result = cls.run_sql(
            "SELECT (SELECT count(*) FROM public.alarms), "
            "(SELECT count(*) FROM public.alarm_events), "
            "(SELECT count(*) FROM public.incidents);"
        )
        if result.returncode:
            raise RuntimeError("Cannot inspect the local test schema. Check the connection and apply the schema first.")
        if result.stdout.strip() != "0|0|0":
            raise RuntimeError("Use an empty disposable local schema; existing data is not a test fixture.")

    @classmethod
    def run_sql(cls, statements):
        script = "\\set VERBOSITY verbose\nBEGIN;\nSET LOCAL statement_timeout = '5s';\n" + statements + "\nROLLBACK;\n"
        return subprocess.run(
            [cls.psql, "-X", "-w", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", *cls.connection_arguments],
            input=script,
            text=True,
            capture_output=True,
            env=cls.connection_env,
            timeout=20,
            check=False,
        )

    @staticmethod
    def sqlstate(result):
        match = re.search(r"ERROR:\s+([0-9A-Z]{5}):", result.stderr)
        return match.group(1) if match else None

    def assert_accepted(self, statements, output=None):
        result = self.run_sql(statements)
        self.assertEqual(result.returncode, 0, f"psql failed with SQLSTATE {self.sqlstate(result)}")
        if output is not None:
            self.assertEqual(result.stdout.strip(), output)

    def assert_rejected(self, statements, state):
        result = self.run_sql(statements)
        self.assertNotEqual(result.returncode, 0, "Invalid data was accepted")
        expected = (state,) if isinstance(state, str) else state
        self.assertIn(self.sqlstate(result), expected, "Unexpected PostgreSQL rejection category")

    def test_valid_defaults_and_nullable_redirect(self):
        self.assert_accepted(
            setup_incident()
            + "SELECT latest_state IS NULL, latest_transition_at IS NULL, revision FROM public.alarms;"
            + "SELECT received_at IS NOT NULL FROM public.alarm_events;"
            + "SELECT incident_state, alarm_ok_at IS NULL, redirect_to_incident_id IS NULL, "
            "created_at IS NOT NULL, updated_at IS NOT NULL FROM public.incidents;",
            "t|t|0\nt\nNEW|t|t|t|t",
        )

    def test_valid_alarm_states(self):
        for state in ("ALARM", "OK", "INSUFFICIENT_DATA"):
            with self.subTest(state=state):
                parent = dict(alarm(), latest_state=state, latest_transition_at=START)
                self.assert_accepted(setup_event(parent, event(parent, state=state)))

    def test_incident_alarm_insufficient_data_and_ok_keep_new_business_state(self):
        self.assert_accepted(
            setup_incident()
            + f"UPDATE public.incidents SET alarm_state='INSUFFICIENT_DATA', last_state_transition_at='{LATER}';"
            + "SELECT alarm_state, alarm_ok_at IS NULL, incident_state FROM public.incidents;"
            + f"UPDATE public.incidents SET alarm_state='OK', last_state_transition_at='{END}', alarm_ok_at='{END}';"
            + "SELECT alarm_state, alarm_ok_at = last_state_transition_at, incident_state FROM public.incidents;",
            "INSUFFICIENT_DATA|t|NEW\nOK|t|NEW",
        )

    def test_latest_state_and_timestamp_must_exist_together(self):
        for fields in ({"latest_state": "ALARM"}, {"latest_transition_at": START}):
            with self.subTest(fields=fields):
                self.assert_rejected(insert("alarms", dict(alarm(), **fields)), "23514")

    def test_ok_timestamp_is_present_exactly_for_ok(self):
        cases = (
            {"alarm_state": "OK"},
            {"alarm_state": "ALARM", "alarm_ok_at": START},
            {"alarm_state": "INSUFFICIENT_DATA", "alarm_ok_at": START},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.assert_rejected(setup_event() + insert("incidents", dict(incident(), **changes)), "23514")

    def test_ok_timestamp_must_match_last_transition(self):
        row = dict(incident(), alarm_state="OK", alarm_ok_at=END, last_state_transition_at=LATER)
        self.assert_rejected(setup_event() + insert("incidents", row), "23514")

    def test_last_transition_cannot_precede_episode_start(self):
        self.assert_rejected(setup_event() + insert("incidents", dict(incident(), last_state_transition_at=PREVIOUS)), "23514")

    def test_unknown_states_are_rejected(self):
        cases = (
            ("alarms", "latest_state", dict(alarm(), latest_transition_at=START), ""),
            ("alarm_events", "state", event(), insert("alarms", alarm())),
            ("alarm_events", "previous_state", event(), insert("alarms", alarm())),
            ("incidents", "alarm_state", incident(), setup_event()),
            ("incidents", "incident_state", incident(), setup_event()),
        )
        for table, column, row, setup in cases:
            with self.subTest(table=table, column=column):
                self.assert_rejected(setup + insert(table, dict(row, **{column: "RESOLVED"})), "23514")

    def test_identifier_format_checks(self):
        cases = (
            ("alarms", "alarm_id", "ALARM#" + "A" * 64, alarm(), ""),
            ("alarms", "alarm_id", "wrong", alarm(), ""),
            ("alarms", "account_id", "12345678901", alarm(), ""),
            ("alarms", "account_id", "12345678901x", alarm(), ""),
            ("alarm_events", "dedupe_key", "A" * 64, event(), insert("alarms", alarm())),
            ("alarm_events", "dedupe_key", "abc", event(), insert("alarms", alarm())),
            ("incidents", "incident_id", "inc_" + "A" * 32, incident(), setup_event()),
            ("incidents", "incident_id", "wrong", incident(), setup_event()),
        )
        for table, column, invalid, row, setup in cases:
            with self.subTest(table=table, column=column, invalid=invalid):
                self.assert_rejected(setup + insert(table, dict(row, **{column: invalid})), "23514")

    def test_declared_string_lengths(self):
        cases = (
            ("alarms", "account_id", 12, alarm(), ""),
            ("alarms", "region", 32, alarm(), ""),
            ("alarms", "alarm_name", 255, alarm(), ""),
            ("alarm_events", "service", 100, event(), insert("alarms", alarm())),
            ("alarm_events", "environment", 32, event(), insert("alarms", alarm())),
            ("incidents", "service", 100, incident(), setup_event()),
            ("incidents", "environment", 32, incident(), setup_event()),
        )
        for table, column, maximum, row, setup in cases:
            with self.subTest(table=table, column=column):
                self.assert_rejected(setup + insert(table, dict(row, **{column: "x" * (maximum + 1)})), "22001")

    def test_required_text_rejects_empty_or_only_plain_spaces(self):
        cases = (
            ("alarms", alarm(), "", ("region", "alarm_arn", "alarm_name")),
            ("alarm_events", event(), insert("alarms", alarm()), ("eventbridge_event_id", "service", "environment")),
            ("incidents", incident(), setup_event(), ("service", "environment")),
        )
        for table, row, setup, columns in cases:
            for column in columns:
                for value in ("", "   "):
                    with self.subTest(table=table, column=column, blank=value):
                        self.assert_rejected(setup + insert(table, dict(row, **{column: value})), "23514")

    def test_required_columns_reject_null_even_when_they_have_defaults(self):
        cases = (
            ("alarms", alarm(), "", ("alarm_id", "account_id", "region", "alarm_arn", "alarm_name", "revision")),
            ("alarm_events", event(), insert("alarms", alarm()), tuple(event()) + ("received_at",)),
            ("incidents", incident(), setup_event(), tuple(incident()) + ("incident_state", "created_at", "updated_at")),
        )
        for table, row, setup, columns in cases:
            for column in columns:
                with self.subTest(table=table, column=column):
                    self.assert_rejected(setup + insert(table, dict(row, **{column: None})), "23502")

    def test_negative_revision_is_rejected(self):
        self.assert_rejected(insert("alarms", dict(alarm(), revision=-1)), "23514")

    def test_previous_transition_must_be_strictly_earlier(self):
        for at in (START, LATER):
            with self.subTest(previous_at=at):
                self.assert_rejected(setup_event(transition=event(previous_at=at)), "23514")

    def test_raw_event_must_be_json_object(self):
        for value in ([], "text", None, 1, True):
            with self.subTest(raw=value):
                row = dict(event(), raw_event=json.dumps(value))
                self.assert_rejected(insert("alarms", alarm()) + insert("alarm_events", row), "23514")

    def test_duplicate_event_key_is_rejected(self):
        self.assert_rejected(setup_event() + insert("alarm_events", event()), "23505")

    def test_different_state_at_same_alarm_transition_is_rejected(self):
        self.assert_rejected(setup_event() + insert("alarm_events", event(state="OK")), "23505")

    def test_eventbridge_id_is_not_the_unique_event_key(self):
        later = event(state="INSUFFICIENT_DATA", at=LATER, previous_state="ALARM", previous_at=START)
        self.assert_accepted(setup_event() + insert("alarm_events", later) + "SELECT count(*) FROM public.alarm_events;", "2")

    def test_missing_parent_alarm_is_rejected(self):
        self.assert_rejected(insert("alarm_events", event()), "23503")
        row = dict(incident(), alarm_id=alarm("missing")["alarm_id"])
        self.assert_rejected(setup_event() + insert("incidents", row), "23503")

    def test_anchor_event_must_exist(self):
        self.assert_rejected(insert("alarms", alarm()) + insert("incidents", incident()), "23503")

    def test_anchor_event_must_belong_to_same_alarm(self):
        other = alarm("schema-test-b")
        row = dict(incident(), alarm_id=other["alarm_id"])
        self.assert_rejected(setup_event() + insert("alarms", other) + insert("incidents", row), "23503")

    def test_redirect_target_must_exist(self):
        row = dict(incident(), redirect_to_incident_id=incident_id(99))
        self.assert_rejected(setup_event() + insert("incidents", row), "23503")

    def test_redirect_target_must_belong_to_same_alarm(self):
        other = alarm("schema-test-b")
        other_event = event(other)
        target = incident(other_event, number=2)
        source = dict(incident(), redirect_to_incident_id=target["incident_id"])
        self.assert_rejected(setup_event() + setup_event(other, other_event) + insert("incidents", target) + insert("incidents", source), "23503")

    def test_redirect_cannot_point_to_itself(self):
        row = dict(incident(), redirect_to_incident_id=incident_id())
        self.assert_rejected(setup_event() + insert("incidents", row), "23514")

    def test_two_canonical_rows_cannot_share_anchor(self):
        self.assert_rejected(setup_incident() + insert("incidents", incident(number=2)), "23505")

    def test_redirect_row_can_share_canonical_anchor(self):
        row = dict(incident(number=2), redirect_to_incident_id=incident_id())
        self.assert_accepted(
            setup_incident() + insert("incidents", row)
            + "SELECT count(*), count(*) FILTER (WHERE redirect_to_incident_id IS NULL) FROM public.incidents;",
            "2|1",
        )

    def test_demote_loser_before_updating_survivor_anchor(self):
        earlier_anchor = event()["dedupe_key"]
        self.assert_accepted(
            two_incidents()
            + f"UPDATE public.incidents SET redirect_to_incident_id='{incident_id()}' WHERE incident_id='{incident_id(2)}';"
            + f"UPDATE public.incidents SET anchor_event_key='{earlier_anchor}', detected_at='{START}' WHERE incident_id='{incident_id()}';"
            + "SELECT count(*), count(DISTINCT anchor_event_key), "
            "count(*) FILTER (WHERE redirect_to_incident_id IS NULL) FROM public.incidents;",
            "2|1|1",
        )

    def test_updating_survivor_anchor_before_demoting_loser_is_rejected(self):
        earlier_anchor = event()["dedupe_key"]
        self.assert_rejected(
            two_incidents() + f"UPDATE public.incidents SET anchor_event_key='{earlier_anchor}', detected_at='{START}' WHERE incident_id='{incident_id()}';",
            "23505",
        )

    def test_referenced_alarm_delete_and_key_update_are_restricted(self):
        # PostgreSQL versions can report RESTRICT as restrict_violation (23001)
        # or foreign_key_violation (23503); both must reject the parent change.
        for mutation in (
            "DELETE FROM public.alarms;",
            f"UPDATE public.alarms SET alarm_id='{alarm('replacement')['alarm_id']}';",
        ):
            with self.subTest(mutation=mutation):
                self.assert_rejected(setup_event() + mutation, ("23001", "23503"))

    def test_referenced_anchor_delete_and_key_update_are_restricted(self):
        for mutation in (
            "DELETE FROM public.alarm_events;",
            f"UPDATE public.alarm_events SET dedupe_key='{digest(['replacement'])}';",
        ):
            with self.subTest(mutation=mutation):
                self.assert_rejected(setup_incident() + mutation, ("23001", "23503"))

    def test_referenced_canonical_delete_and_key_update_are_restricted(self):
        redirect = dict(incident(number=2), redirect_to_incident_id=incident_id())
        for mutation in (
            f"DELETE FROM public.incidents WHERE incident_id='{incident_id()}';",
            f"UPDATE public.incidents SET incident_id='{incident_id(99)}' WHERE incident_id='{incident_id()}';",
        ):
            with self.subTest(mutation=mutation):
                self.assert_rejected(setup_incident() + insert("incidents", redirect) + mutation, ("23001", "23503"))

    def test_same_detected_time_different_anchors_are_not_unique_by_time(self):
        self.assert_accepted(
            two_incidents() + f"UPDATE public.incidents SET detected_at='{START}';"
            + "SELECT count(*), count(DISTINCT detected_at), count(DISTINCT anchor_event_key) FROM public.incidents;",
            "2|1|2",
        )


if __name__ == "__main__":
    unittest.main()
