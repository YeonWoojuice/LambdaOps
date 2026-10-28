"""Configuration and IAM wiring tests; no real credentials or network calls."""

import unittest
from unittest.mock import Mock, patch

from lambdaops_m3.dsql_connection import DsqlConnectionFactory, DsqlSettings, build_store


ENV = {
    "INCIDENT_STORE": "dsql", "DSQL_ENDPOINT": "testcluster.dsql.ap-northeast-2.on.aws",
    "DSQL_REGION": "ap-northeast-2", "DSQL_DB_USER": "lambdaops_ingest",
}


class DsqlConnectionTests(unittest.TestCase):
    def test_settings_require_matching_region_and_custom_role(self):
        for values in (
            {"DSQL_ENDPOINT": "localhost"}, {"DSQL_REGION": "us-east-1"},
            {"DSQL_DB_USER": "admin"}, {"DSQL_DB_USER": ""},
            {"DSQL_CONNECT_TIMEOUT": "0"}, {"DSQL_CONNECT_TIMEOUT": "abc"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                DsqlSettings.from_environ(dict(ENV, **values))

    def test_token_refresh_tls_verification_and_snapshot_isolation(self):
        import psycopg
        client = Mock()
        client.generate_db_connect_auth_token.side_effect = ["test-token-one", "test-token-two"]
        connect = Mock(side_effect=[Mock(), Mock()])
        factory = DsqlConnectionFactory(DsqlSettings.from_environ(ENV), client=client,
                                        connect=connect, read_only=True)
        first, second = factory(), factory()
        self.assertEqual(client.generate_db_connect_auth_token.call_count, 2)
        self.assertEqual([c.kwargs["password"] for c in connect.call_args_list],
                         ["test-token-one", "test-token-two"])
        for call in connect.call_args_list:
            self.assertEqual(call.kwargs["sslmode"], "verify-full")
            self.assertTrue(call.kwargs["sslrootcert"])
            self.assertFalse(call.kwargs["autocommit"])
            self.assertEqual(call.kwargs["dbname"], "postgres")
        for conn in (first, second):
            self.assertEqual(conn.isolation_level, psycopg.IsolationLevel.REPEATABLE_READ)
            self.assertTrue(conn.read_only)

    def test_dsql_selection_is_explicit_and_build_does_not_connect(self):
        with patch("lambdaops_m3.dsql_connection.DsqlConnectionFactory") as factory:
            store = build_store(env=ENV, read_only=True)
            self.assertTrue(store.supports_page_queries)
            factory.assert_called_once_with(DsqlSettings.from_environ(ENV), read_only=True)
            factory.return_value.assert_not_called()
        with self.assertRaises(ValueError):
            build_store(env={"INCIDENT_STORE": "typo"})

    def test_default_keeps_existing_dynamo_infrastructure_compatible(self):
        with patch("lambdaops_m3.store.DynamoIncidentStore") as constructor:
            build_store(env={"INCIDENTS_TABLE_NAME": "test-table"})
            constructor.assert_called_once_with("test-table")
