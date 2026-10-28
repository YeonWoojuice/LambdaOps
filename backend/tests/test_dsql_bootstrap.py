"""Offline bootstrap checks: target validation, drift rejection and role boundary."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_dsql.py"
SPEC = importlib.util.spec_from_file_location("lambdaops_bootstrap_tests", SCRIPT)
bootstrap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bootstrap
SPEC.loader.exec_module(bootstrap)

DATA = {
    "account_id": "123456789012", "region": "ap-northeast-2", "cluster_id": "a" * 26,
    "cluster_arn": "arn:aws:dsql:ap-northeast-2:123456789012:cluster/" + "a" * 26,
    "endpoint": "a" * 26 + ".dsql.ap-northeast-2.on.aws",
    "ingest_iam_role_arn": "arn:aws:iam::123456789012:role/lambdaops-ingest",
    "read_iam_role_arn": "arn:aws:iam::123456789012:role/lambdaops-read",
    "ingest_db_role": "lambdaops_ingest", "read_db_role": "lambdaops_read",
}


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def catalog_rows():
    expected = bootstrap.expected_schema()
    columns = []
    for table, entries in expected["columns"].items():
        for ordinal, (name, kind, size, precision, nullable, default) in enumerate(entries, 1):
            columns.append(dict(table_name=table, column_name=name, data_type=kind,
                character_maximum_length=size, datetime_precision=precision,
                is_nullable=nullable, column_default=default or None, ordinal_position=ordinal))
    constraints = [dict(table_name=t, name=n, definition=d, valid=True)
                   for (t,n),d in expected["raw_constraints"].items()]
    indexes = []
    for (table, name), spec in expected["indexes"].items():
        ordinals = {column[0]: n for n, column in enumerate(expected["columns"][table], 1)}
        indexes.append(dict(table_name=table, name=name, valid=True, ready=True,
            unique_index=spec["unique"], primary_index=spec["primary"], method="btree",
            key_count=len(spec["keys"]), attribute_count=len(spec["keys"]),
            attribute_numbers=" ".join(str(ordinals[x]) for x in spec["keys"]),
            options=" ".join("0" for _ in spec["keys"]), expressions=None,
            predicate=("redirect_to_incident_id IS NOT NULL" if name == "idx_incidents_redirect_target"
                       else "redirect_to_incident_id IS NULL") if spec["predicate"] else None))
    return columns, constraints, indexes


class ConfigTests(unittest.TestCase):
    def test_accepts_direct_and_wrapped_terraform_outputs(self):
        expected = bootstrap.Config.parse(DATA)
        self.assertEqual(bootstrap.Config.parse({"value": DATA}), expected)
        self.assertEqual(bootstrap.Config.parse({"dsql_bootstrap": {"value": DATA}}), expected)

    def test_rejects_account_endpoint_role_and_identifier_mismatch(self):
        for changed in (
            {"account_id": "123"}, {"endpoint": "evil.example"},
            {"region": "us-east-1"}, {"cluster_id": "not-a-cluster"},
            {"ingest_iam_role_arn": "arn:aws:iam::999999999999:role/writer"},
            {"ingest_db_role": "admin"}, {"ingest_db_role": "x; DROP ROLE admin"},
            {"read_db_role": "lambdaops_ingest"}, {"read_iam_role_arn": DATA["ingest_iam_role_arn"]},
        ):
            with self.subTest(changed=changed), self.assertRaises(bootstrap.BootstrapError):
                bootstrap.Config.parse(dict(DATA, **changed))

    def test_preview_is_offline_and_does_not_print_a_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(DATA))
            with patch.object(bootstrap, "connect_admin") as connect, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(bootstrap.main(["preview", "--config", str(path)]), 0)
            connect.assert_not_called()
            rendered = json.loads(output.getvalue())
            self.assertFalse(rendered["aws_contacted"])
            self.assertEqual(len(rendered["schema_files"]), 7)
            self.assertNotIn("password", output.getvalue().lower())

    def test_driver_failure_redacts_details(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(DATA))
            with patch.object(bootstrap, "connect_admin", side_effect=RuntimeError("password=secret-token")):
                with redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(bootstrap.main(["verify", "--config", str(path)]), 1)
            self.assertNotIn("secret-token", errors.getvalue())
            self.assertIn("RuntimeError", errors.getvalue())


class SchemaTests(unittest.TestCase):
    def test_manifest_matches_approved_schema(self):
        schema = bootstrap.expected_schema()
        self.assertEqual([len(schema["columns"][t]) for t in bootstrap.TABLES], [8,11,13])
        self.assertEqual(len(schema["constraints"]), 35)
        self.assertEqual(len(schema["indexes"]), 10)

    def test_normalizes_postgresql_format_without_changing_literal_meaning(self):
        normal = bootstrap.normalized_definition
        self.assertEqual(normal("CHECK (state IN ('ALARM','OK','INSUFFICIENT_DATA'))"),
                         normal("CHECK (((state)::text = ANY ((ARRAY['ALARM'::character varying, 'OK'::character varying, 'INSUFFICIENT_DATA'::character varying])::text[])))"))
        self.assertNotEqual(normal("CHECK (state='NEW')"), normal("CHECK (state='new')"))
        self.assertNotEqual(normal("CHECK (name <> '')"), normal("CHECK (name <> ' ')"))
        self.assertNotEqual(normal("CHECK (a AND (b OR c))"), normal("CHECK ((a AND b) OR c)"))
        self.assertEqual(normal("NULL::character varying"), normal(None))

    def verify(self, rows):
        connection = Mock()
        connection.execute.side_effect = [Cursor(r) for r in rows]
        return bootstrap.verify_schema(connection)

    def test_verifies_full_schema_not_only_counts(self):
        self.assertEqual(self.verify(catalog_rows()), {"tables":3,"columns":32,"constraints":35,"indexes":10})

    def test_rejects_missing_index_or_invalid_build(self):
        for mutation in (lambda rows: rows[2].pop(), lambda rows: rows[2][0].update(valid=False),
                         lambda rows: rows[2][0].update(ready=False)):
            rows = catalog_rows()
            mutation(rows)
            with self.assertRaises(bootstrap.BootstrapError):
                self.verify(rows)

    def test_rejects_same_named_check_index_or_column_drift(self):
        for group, key, value in ((0,"is_nullable","YES"), (1,"definition","CHECK (true)"),
                                  (2,"attribute_numbers","2")):
            rows = catalog_rows()
            rows[group][0][key] = value
            with self.assertRaises(bootstrap.BootstrapError):
                self.verify(rows)

    def test_dsql_primary_covering_index_and_btree_index_method(self):
        rows = catalog_rows()
        # Independent metadata shape from DSQL's documented primary-index
        # representation: alarm_id is the sole key, remaining columns INCLUDE.
        rows[2][0].update(method="btree_index", attribute_numbers="1 2 3 4 5 6 7 8",
                          attribute_count=8, key_count=1, options="0")
        self.assertEqual(self.verify(rows)["indexes"], 10)
        rows[2][0].update(attribute_numbers="1 2", attribute_count=2)
        with self.assertRaises(bootstrap.BootstrapError):
            self.verify(rows)

    def test_dsql_primary_constraint_full_include_only(self):
        rows = catalog_rows()
        primary = next(row for row in rows[1] if row["name"] == "pk_alarms")
        # Actual DSQL pg_get_constraintdef also exposes its PK INCLUDE columns.
        primary["definition"] = ("PRIMARY KEY (alarm_id) INCLUDE (account_id, region, "
            "alarm_arn, alarm_name, latest_state, latest_transition_at, revision)")
        self.assertEqual(self.verify(rows)["constraints"], 35)
        for definition in (
            "PRIMARY KEY (alarm_id) INCLUDE (account_id)",
            "PRIMARY KEY (alarm_id) INCLUDE (account_id, region, alarm_arn, alarm_name, latest_state, latest_transition_at, revision, revision)",
            "PRIMARY KEY (account_id) INCLUDE (alarm_id, region, alarm_arn, alarm_name, latest_state, latest_transition_at, revision)",
            "UNIQUE (alarm_id) INCLUDE (account_id, region, alarm_arn, alarm_name, latest_state, latest_transition_at, revision)",
        ):
            primary["definition"] = definition
            with self.subTest(definition=definition), self.assertRaises(bootstrap.BootstrapError):
                self.verify(rows)

    def test_rejects_index_uniqueness_order_and_predicate_drift(self):
        for changes in ({"unique_index":False}, {"options":"1"}, {"method":"hash"},
                        {"predicate":"alarm_id IS NULL"}):
            rows = catalog_rows()
            rows[2][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(bootstrap.BootstrapError):
                self.verify(rows)

    def test_existing_or_partial_schema_is_never_modified(self):
        connection = Mock()
        connection.execute.return_value = Cursor([{"tablename":"alarms"}])
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.initialize_schema(connection)
        self.assertEqual(connection.execute.call_count, 1)

    def test_async_wait_requires_explicit_completed_status(self):
        connection = Mock()
        connection.execute.side_effect = [Cursor([{"status":"processing"}]), Cursor([{"status":"completed"}])]
        sleep = Mock()
        bootstrap.wait_for_index(connection, "job-1", 5, clock=lambda:0, sleep=sleep)
        sleep.assert_called_once()
        for status in ("failed", "false", None):
            connection.execute.side_effect = None
            connection.execute.return_value = Cursor([] if status is None else [{"status":status}])
            with self.subTest(status=status), self.assertRaises(bootstrap.BootstrapError):
                bootstrap.wait_for_index(connection,"job-1",5,clock=lambda:0,sleep=sleep)

    def test_async_timeout_is_not_treated_as_success(self):
        connection = Mock()
        connection.execute.return_value = Cursor([{"status":"processing"}])
        clock = Mock(side_effect=[0, 6])
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.wait_for_index(connection,"job-1",5,clock=clock,sleep=Mock())


class RoleTests(unittest.TestCase):
    def role_catalog(self, *, grant_option=False, cross_mapping=False):
        config = bootstrap.Config.parse(DATA)
        roles = (config.ingest_db_role, config.read_db_role)
        permissions = bootstrap.expected_permissions(config)

        def execute(query, params=None):
            if "pg_auth_members" in query:
                return Cursor([])
            if "pg_catalog.pg_roles" in query:
                return Cursor([dict(rolname=role, rolcanlogin=True, rolsuper=False,
                    rolcreaterole=False, rolcreatedb=False, rolreplication=False,
                    rolbypassrls=False) for role in roles])
            if "iam_pg_role_mappings" in query:
                rows = [dict(arn=config.ingest_iam_role_arn, pg_role_name=roles[0]),
                        dict(arn=config.read_iam_role_arn, pg_role_name=roles[1])]
                if cross_mapping:
                    rows.append(dict(arn=config.read_iam_role_arn, pg_role_name=roles[0]))
                return Cursor(rows)
            if "has_schema_privilege" in query:
                return Cursor([{"allowed":False}] if "GRANT OPTION" in query else
                              [{"usage":True,"create_allowed":False}])
            if "has_table_privilege" in query:
                role, table, privilege = params
                allowed = ((grant_option and privilege == "SELECT WITH GRANT OPTION")
                    if "GRANT OPTION" in privilege
                    else privilege in permissions[(role, table.removeprefix("public."))])
                return Cursor([{"allowed":allowed}])
            raise AssertionError("Unexpected catalog query")

        return Mock(execute=Mock(side_effect=execute)), config

    def test_verifies_effective_permissions_without_delegation_or_cross_mapping(self):
        connection, config = self.role_catalog()
        self.assertEqual(bootstrap.verify_roles(connection, config)["privileges"], "matched")
        for changes in ({"grant_option":True}, {"cross_mapping":True}):
            connection, config = self.role_catalog(**changes)
            with self.subTest(changes=changes), self.assertRaises(bootstrap.BootstrapError):
                bootstrap.verify_roles(connection, config)

    def test_grants_separate_login_roles_without_mutating_evidence_or_deleting(self):
        statements = bootstrap.role_statements(bootstrap.Config.parse(DATA))
        self.assertIn('CREATE ROLE "lambdaops_ingest" WITH LOGIN', statements)
        self.assertIn('CREATE ROLE "lambdaops_read" WITH LOGIN', statements)
        self.assertFalse(any("ON SCHEMA" in statement for statement in statements))
        self.assertEqual(sum(statement.startswith("REVOKE ALL ON TABLE") for statement in statements), 3)
        self.assertIn('GRANT SELECT, INSERT ON TABLE public.alarm_events TO "lambdaops_ingest"', statements)
        grants = [statement for statement in statements if statement.startswith("GRANT")]
        self.assertFalse(any("DELETE" in x or "TRUNCATE" in x or "WITH GRANT OPTION" in x for x in grants))
        self.assertFalse(any("UPDATE" in x and "alarm_events" in x for x in grants))
        self.assertFalse(any(("INSERT" in x or "UPDATE" in x) and '"lambdaops_read"' in x for x in grants))
        mappings = [statement for statement in statements if statement.startswith("AWS IAM")]
        self.assertEqual(mappings, [
            'AWS IAM GRANT "lambdaops_ingest" TO ' + bootstrap.quote_literal(DATA["ingest_iam_role_arn"]),
            'AWS IAM GRANT "lambdaops_read" TO ' + bootstrap.quote_literal(DATA["read_iam_role_arn"]),
        ])

    def test_managed_public_schema_is_verified_without_changing_its_acl(self):
        connection = Mock()
        connection.execute.return_value = Cursor([{"usage":True,"create_allowed":True}])
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.verify_public_schema_permissions(connection, bootstrap.Config.parse(DATA))
        self.assertEqual(connection.execute.call_count, 1)
        self.assertTrue(connection.execute.call_args.args[0].startswith("SELECT"))

    def test_existing_roles_are_checked_not_repaired(self):
        connection = Mock()
        connection.execute.side_effect = [Cursor([{"tablename":x} for x in bootstrap.TABLES]),
                                           Cursor([{"rolname":"lambdaops_read"}])]
        with patch.object(bootstrap,"verify_schema"), patch.object(bootstrap,"verify_roles", side_effect=bootstrap.BootstrapError("partial")):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.initialize_roles(connection,bootstrap.Config.parse(DATA))
        self.assertEqual(connection.execute.call_count,2)

    def test_unexpected_existing_iam_mapping_blocks_role_creation(self):
        connection = Mock()
        connection.execute.side_effect = [Cursor([{"tablename":x} for x in bootstrap.TABLES]), Cursor([]), Cursor([{"arn":DATA["read_iam_role_arn"]}])]
        with patch.object(bootstrap,"verify_schema"), self.assertRaises(bootstrap.BootstrapError):
            bootstrap.initialize_roles(connection,bootstrap.Config.parse(DATA))
        self.assertEqual(connection.execute.call_count,3)


if __name__ == "__main__":
    unittest.main()
