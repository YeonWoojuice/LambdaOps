"""Offline checks for the controlled, clearly labeled DSQL demo seed."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from lambdaops_m3.contracts import parse_eventbridge_alarm


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo_incident.py"
SPEC = importlib.util.spec_from_file_location("lambdaops_seed_demo_tests", SCRIPT)
seed = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = seed
SPEC.loader.exec_module(seed)

DATA = {
    "account_id": "123456789012", "region": "ap-northeast-2", "cluster_id": "a" * 26,
    "cluster_arn": "arn:aws:dsql:ap-northeast-2:123456789012:cluster/" + "a" * 26,
    "endpoint": "a" * 26 + ".dsql.ap-northeast-2.on.aws",
    "ingest_iam_role_arn": "arn:aws:iam::123456789012:role/lambdaops-ingest",
    "read_iam_role_arn": "arn:aws:iam::123456789012:role/lambdaops-read",
    "ingest_db_role": "lambdaops_ingest", "read_db_role": "lambdaops_read",
}
SEED_ID = "ad98f8d7-f9a4-47f9-a851-1a31eaa6d9bb"
AT = "2026-10-29T00:00:00.123456Z"


class SeedDemoIncidentTests(unittest.TestCase):
    def test_synthetic_event_meets_contract_and_identifies_test_provenance(self):
        config = seed.Config.parse(DATA)
        event = seed.build_demo_event(config, UUID(SEED_ID), datetime.fromisoformat(AT.replace("Z", "+00:00")))
        transition = parse_eventbridge_alarm(
            event, {event["resources"][0]}, seed.SERVICE, seed.ENVIRONMENT
        )
        self.assertEqual(transition.state, "ALARM")
        self.assertEqual(transition.previous_state, "OK")
        self.assertEqual(transition.transitioned_at, AT)
        self.assertIn("DEMO", transition.alarm_name)
        self.assertIn("DEMO", transition.event_id)
        self.assertIn("DEMO", event["detail"]["state"]["reason"])
        self.assertIn("DEMO", json.dumps(event))
        self.assertIn("not a CloudWatch", event["lambdaopsDemoProvenance"])
        other = seed.build_demo_event(config, UUID("316c1f02-cf79-4bdb-b71b-e28450ba54b6"),
                                      datetime.now(timezone.utc))
        self.assertNotEqual(event["resources"][0], other["resources"][0])

    def test_preview_reads_only_named_terraform_output_and_never_connects(self):
        with tempfile.TemporaryDirectory() as directory:
            terraform_output = Mock(stdout=json.dumps(DATA))
            with patch.object(seed.subprocess, "run", return_value=terraform_output) as run, \
                 patch.object(seed, "connect_admin") as connect, \
                 patch.object(seed, "DsqlIncidentStore") as store, \
                 redirect_stdout(io.StringIO()) as output:
                code = seed.main(["--terraform-root", directory, "--seed-id", SEED_ID,
                                  "--transitioned-at", AT])
            self.assertEqual(code, 0)
            run.assert_called_once_with(
                ["terraform", "output", "-json", "dsql_bootstrap"],
                cwd=Path(directory), capture_output=True, text=True, check=True,
            )
            connect.assert_not_called()
            store.assert_not_called()
            rendered = json.loads(output.getvalue())
            self.assertEqual(rendered["mode"], "preview")
            self.assertFalse(rendered["aws_write"])
            self.assertEqual(rendered["seed_id"], SEED_ID)
            self.assertEqual(rendered["transitioned_at"], AT)
            self.assertNotIn("password", output.getvalue().lower())

    def test_apply_refuses_unconfirmed_target_before_connecting(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(seed, "load_config", return_value=seed.Config.parse(DATA)), \
                 patch.object(seed, "connect_admin") as connect, \
                 patch.object(seed, "DsqlIncidentStore") as store, \
                 redirect_stderr(io.StringIO()) as errors:
                code = seed.main([
                    "--terraform-root", directory, "--seed-id", SEED_ID,
                    "--transitioned-at", AT, "--apply", "--expected-account-id", "999999999999",
                    "--expected-region", DATA["region"],
                    "--expected-cluster-id", DATA["cluster_id"],
                ])
            self.assertEqual(code, 2)
            self.assertIn("does not match", errors.getvalue())
            connect.assert_not_called()
            store.assert_not_called()

    def test_admin_connection_is_transactional_before_store_receives_it(self):
        connection = Mock()
        fake_psycopg = SimpleNamespace(
            IsolationLevel=SimpleNamespace(REPEATABLE_READ="repeatable-read")
        )
        config = seed.Config.parse(DATA)
        with patch.dict(sys.modules, {"psycopg": fake_psycopg}), \
             patch.object(seed, "connect_admin", return_value=connection) as connect:
            result = seed._connection_factory(config, "demo-profile")
        self.assertIs(result, connection)
        connect.assert_called_once_with(config, "demo-profile")
        self.assertIs(connection.autocommit, False)
        self.assertEqual(connection.isolation_level, "repeatable-read")
        connection.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
