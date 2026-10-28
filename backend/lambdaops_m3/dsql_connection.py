"""Short-lived IAM-authenticated DSQL connections; no credentials in settings."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class DsqlSettings:
    endpoint: str
    region: str
    user: str
    connect_timeout: int = 10
    sslrootcert: Optional[str] = None

    @classmethod
    def from_environ(cls, env: Mapping[str, str]) -> "DsqlSettings":
        endpoint = env.get("DSQL_ENDPOINT", "")
        region = env.get("DSQL_REGION") or env.get("AWS_REGION", "")
        user = env.get("DSQL_DB_USER", "")
        if not region or not re.fullmatch(r"[a-z0-9-]+", region):
            raise ValueError("DSQL_REGION or AWS_REGION is required")
        if not re.fullmatch(r"[a-z0-9-]+\.dsql\." + re.escape(region) + r"\.on\.aws", endpoint):
            raise ValueError("DSQL_ENDPOINT must be a cluster endpoint in DSQL_REGION")
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", user) or user in {"admin", "postgres"}:
            raise ValueError("DSQL_DB_USER must be a custom runtime database role")
        try:
            timeout = int(env.get("DSQL_CONNECT_TIMEOUT", "10"))
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid DSQL_CONNECT_TIMEOUT") from exc
        if not 1 <= timeout <= 30:
            raise ValueError("Invalid DSQL_CONNECT_TIMEOUT")
        root = env.get("DSQL_SSLROOTCERT") or None
        return cls(endpoint, region, user, timeout, root)


class DsqlConnectionFactory:
    """Each transaction gets a fresh token and connection, including retries.

    The custom database role must be mapped to the Lambda IAM role separately.
    Connection contexts commit/rollback and close; no connection is kept while
    a user changes pages. Local tests inject their own factory, never AWS IAM.
    """

    def __init__(self, settings: DsqlSettings, *, read_only: bool = False,
                 client: Any = None, connect: Any = None) -> None:
        self.settings = settings
        self.read_only = read_only
        self._client = client
        self._connect = connect

    def __call__(self) -> Any:
        import certifi
        import psycopg
        from psycopg.rows import dict_row

        if self._client is None:
            import boto3
            self._client = boto3.client("dsql", region_name=self.settings.region)
        token = self._client.generate_db_connect_auth_token(
            Hostname=self.settings.endpoint, Region=self.settings.region, ExpiresIn=900,
        )
        connection = (self._connect or psycopg.connect)(
            host=self.settings.endpoint, port=5432, dbname="postgres",
            user=self.settings.user, password=token,
            sslmode="verify-full", sslrootcert=self.settings.sslrootcert or certifi.where(),
            connect_timeout=self.settings.connect_timeout,
            application_name="lambdaops-m3-read" if self.read_only else "lambdaops-m3-ingest",
            autocommit=False, row_factory=dict_row, prepare_threshold=None,
        )
        try:
            connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            connection.read_only = self.read_only
        except Exception:
            connection.close()
            raise
        return connection


def build_store(*, read_only: bool = False, env: Optional[Mapping[str, str]] = None) -> Any:
    """Explicit DSQL opt-in until Terraform and frontend contracts migrate."""
    settings = os.environ if env is None else env
    backend = settings.get("INCIDENT_STORE", "dynamodb")
    if backend == "dynamodb":
        from .store import DynamoIncidentStore
        return DynamoIncidentStore(settings["INCIDENTS_TABLE_NAME"])
    if backend == "dsql":
        from .dsql_store import DsqlIncidentStore
        return DsqlIncidentStore(DsqlConnectionFactory(
            DsqlSettings.from_environ(settings), read_only=read_only,
        ))
    raise ValueError("INCIDENT_STORE must be dynamodb or dsql")
