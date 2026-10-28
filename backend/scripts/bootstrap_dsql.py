"""Explicit initial DSQL schema and runtime-role setup outside Terraform state.

Preview is offline. Other commands use the operator's IAM admin connection,
never Lambda credentials. Every DDL/DCL statement is committed separately.
Existing partial objects require inspection; this tool never drops or repairs
them automatically. Tokens, connection strings and raw database errors are not
printed. Run from a trusted checkout with its approved SQL files.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping


SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "dsql"
TABLES = ("alarms", "alarm_events", "incidents")
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


class BootstrapError(RuntimeError):
    """A safe, actionable message that contains no credentials."""


@dataclass(frozen=True)
class Config:
    account_id: str
    region: str
    cluster_id: str
    cluster_arn: str
    endpoint: str
    ingest_iam_role_arn: str
    read_iam_role_arn: str
    ingest_db_role: str
    read_db_role: str

    @classmethod
    def parse(cls, raw: Any) -> "Config":
        if isinstance(raw, dict) and "dsql_bootstrap" in raw:
            raw = raw["dsql_bootstrap"]
        if isinstance(raw, dict) and "value" in raw:
            raw = raw["value"]
        if not isinstance(raw, dict):
            raise BootstrapError("Expected the non-null dsql_bootstrap Terraform output")
        names = tuple(cls.__dataclass_fields__)
        if any(not isinstance(raw.get(name), str) for name in names):
            raise BootstrapError("The bootstrap configuration is missing string fields")
        result = cls(**{name: raw[name] for name in names})
        if not re.fullmatch(r"[0-9]{12}", result.account_id):
            raise BootstrapError("Invalid expected AWS account")
        if not re.fullmatch(r"[a-z0-9-]+", result.region):
            raise BootstrapError("Invalid AWS region")
        if not re.fullmatch(r"[a-z0-9]{26}", result.cluster_id):
            raise BootstrapError("Invalid DSQL cluster identifier")
        expected_arn = f"arn:aws:dsql:{result.region}:{result.account_id}:cluster/{result.cluster_id}"
        if result.cluster_arn != expected_arn:
            raise BootstrapError("Cluster ARN disagrees with account, region or identifier")
        if result.endpoint != f"{result.cluster_id}.dsql.{result.region}.on.aws":
            raise BootstrapError("Endpoint disagrees with cluster identifier or region")
        for arn in (result.ingest_iam_role_arn, result.read_iam_role_arn):
            if not re.fullmatch(r"arn:aws:iam::" + result.account_id + r":role/[A-Za-z0-9_+=,.@/-]+", arn):
                raise BootstrapError("Runtime IAM roles must belong to the expected account")
        for role in (result.ingest_db_role, result.read_db_role):
            if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role) or role in {"admin", "postgres", "public"}:
                raise BootstrapError("Runtime database role names must be custom SQL identifiers")
        if result.ingest_iam_role_arn == result.read_iam_role_arn or result.ingest_db_role == result.read_db_role:
            raise BootstrapError("Read and ingest identities must be separate")
        return result


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def role_statements(config: Config) -> list[str]:
    writer, reader = map(quote_identifier, (config.ingest_db_role, config.read_db_role))
    # DSQL manages public: custom roles already have USAGE and cannot CREATE.
    # Its managed ACL rejects PostgreSQL-style schema GRANT/REVOKE operations.
    statements = [f"CREATE ROLE {writer} WITH LOGIN", f"CREATE ROLE {reader} WITH LOGIN"]
    statements += [f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC" for table in TABLES]
    statements += [f"GRANT SELECT, INSERT, UPDATE ON TABLE public.{table} TO {writer}"
                   for table in ("alarms", "incidents")]
    statements += [f"GRANT SELECT, INSERT ON TABLE public.alarm_events TO {writer}"]
    statements += [f"GRANT SELECT ON TABLE public.{table} TO {reader}" for table in TABLES]
    statements += [f"AWS IAM GRANT {writer} TO {quote_literal(config.ingest_iam_role_arn)}",
                   f"AWS IAM GRANT {reader} TO {quote_literal(config.read_iam_role_arn)}"]
    return statements


def _without_comments(value: str) -> str:
    return re.sub(r"--[^\n]*", "", value).strip().rstrip(";").strip()


def _items(value: str) -> list[str]:
    """Split this repository's simple table declarations, respecting SQL strings."""
    result, start, depth, quoted = [], 0, 0, False
    for index, char in enumerate(value):
        if char == "'":
            quoted = not quoted
        elif not quoted:
            depth += (char == "(") - (char == ")")
            if char == "," and depth == 0:
                result.append(value[start:index].strip())
                start = index + 1
    result.append(value[start:].strip())
    return result


def _tokens(value: str) -> list[str]:
    # Preserve quoted contents exactly; only the known harmless casts emitted by
    # pg_get_constraintdef for our varchar/text/bigint expressions are removed.
    pattern = r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|::(?:character varying|timestamp with time zone|text|bigint)(?:\[\])?|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|<>|>=|<=|[=<>~(),\[\].;]"
    result, end = [], 0
    for match in re.finditer(pattern, value, re.IGNORECASE):
        if value[end:match.start()].strip():
            raise BootstrapError("Unrecognized catalog expression; inspect before proceeding")
        token = match[0]
        if not token.startswith("::"):
            result.append(token if token.startswith(("'", '\"')) else token.lower())
        end = match.end()
    if value[end:].strip():
        raise BootstrapError("Unrecognized catalog expression; inspect before proceeding")
    return result


def normalized_expression(value: str) -> Any:
    """Parse our small approved SQL expression subset without losing grouping."""
    tokens, position = _tokens(value), 0

    def peek():
        return tokens[position] if position < len(tokens) else None

    def take(expected=None):
        nonlocal position
        token = peek()
        if token is None or (expected is not None and token != expected):
            raise BootstrapError("Unsupported expression structure in schema catalog")
        position += 1
        return token

    def sequence(close):
        values = []
        if peek() != close:
            values.append(expression())
            while peek() == ",":
                take(",")
                values.append(expression())
        take(close)
        return tuple(values)

    def expression(minimum=0):
        token = take()
        if token == "(":
            left = expression()
            take(")")
        elif token == "array":
            take("[")
            left = ("array", sequence("]"))
        elif peek() == "(":
            take("(")
            left = ("call", token, sequence(")"))
        elif token not in {"and", "or", "is", "in", ")", "]", ","}:
            left = ("atom", token)
        else:
            raise BootstrapError("Unsupported expression atom in schema catalog")
        precedence = {"or": 1, "and": 2, "=": 3, "<>": 3, ">=": 3, "<=": 3,
                      ">": 3, "<": 3, "~": 3, "is": 3, "in": 3}
        while peek() in precedence and precedence[peek()] >= minimum:
            operator = take()
            if operator == "is":
                negate = peek() == "not"
                if negate:
                    take("not")
                take("null")
                left = ("is_not_null" if negate else "is_null", left)
            elif operator == "in":
                take("(")
                left = ("in", left, sequence(")"))
            else:
                right = expression(precedence[operator] + 1)
                if operator == "=" and right[:2] == ("call", "any") and len(right[2]) == 1 and right[2][0][0] == "array":
                    left = ("in", left, right[2][0][1])
                else:
                    left = (operator, left, right)
        return left

    result = expression()
    if position != len(tokens):
        raise BootstrapError("Unsupported trailing expression in schema catalog")
    return result


def normalized_definition(value: str | None) -> str:
    if value is None:
        return ""
    tokens = _tokens(value)
    if tokens == ["null"]:
        return ""  # PostgreSQL may omit an explicit DEFAULT NULL in its catalog.
    if tokens and tokens[0] == "check":
        return repr(normalized_expression(value[value.lower().index("check") + 5:]))
    # FK/PK/UNIQUE syntax and literal defaults retain parentheses. Only the
    # default MATCH SIMPLE spelling, schema qualifier and action order differ.
    value = " ".join(tokens)
    value = value.replace("match simple", "").replace("public . ", "")
    value = value.replace("on delete restrict on update restrict", "on update restrict on delete restrict")
    return " ".join(value.split())


def expected_schema(sql_dir: Path = SQL_DIR) -> dict[str, Any]:
    columns, constraints, indexes, raw_constraints = {}, {}, {}, {}
    paths = sorted(sql_dir.glob("00[1-7]_*.sql"))
    if len(paths) != 7:
        raise BootstrapError("Expected exactly the seven approved schema SQL files")
    for path in paths[:3]:
        sql = _without_comments(path.read_text())
        match = re.fullmatch(r"CREATE TABLE public\.(\w+)\s*\((.*)\)", sql, re.DOTALL)
        if match is None or match[1] not in TABLES:
            raise BootstrapError("Unsupported table DDL; inspect the approved SQL")
        table, body = match[1], match[2]
        columns[table] = []
        for part in _items(body):
            if part.startswith("CONSTRAINT "):
                name, definition = part[len("CONSTRAINT "):].split(None, 1)
                constraints[(table, name)] = normalized_definition(definition)
                raw_constraints[(table, name)] = definition
                key = re.fullmatch(r"(?:PRIMARY KEY|UNIQUE)\s*\(([^)]+)\)", definition)
                if key:
                    indexes[(table, name)] = {"keys": tuple(x.strip() for x in key[1].split(",")),
                        "unique": True, "primary": definition.startswith("PRIMARY KEY"), "predicate": None}
                continue
            column = re.fullmatch(r"(\w+)\s+(VARCHAR\((\d+)\)|TIMESTAMPTZ\(6\)|TEXT|BIGINT|JSONB)(.*)",
                                  part, re.DOTALL)
            if column is None:
                raise BootstrapError("Unsupported column declaration; inspect the approved SQL")
            name, kind, length, tail = column.groups()
            data_type = ("character varying" if length else {
                "TIMESTAMPTZ(6)": "timestamp with time zone", "TEXT": "text",
                "BIGINT": "bigint", "JSONB": "jsonb",
            }[kind])
            default = tail.split("DEFAULT", 1)[1].strip() if "DEFAULT" in tail else None
            columns[table].append((name, data_type, int(length) if length else None,
                                   6 if kind == "TIMESTAMPTZ(6)" else None,
                                   "NO" if "NOT NULL" in tail else "YES", normalized_definition(default)))
    for path in paths[3:]:
        statement = _without_comments(path.read_text())
        match = re.fullmatch(r"CREATE (UNIQUE )?INDEX ASYNC (\w+)\s+ON public\.(\w+)\s*\(([^)]+)\)\s+WHERE (.*)", statement, re.DOTALL)
        if match is None:
            raise BootstrapError("Unsupported index DDL; inspect the approved SQL")
        indexes[(match[3], match[2])] = {"keys": tuple(x.strip() for x in match[4].split(",")),
            "unique": bool(match[1]), "primary": False, "predicate": normalized_expression(match[5])}
    return {"columns": columns, "constraints": constraints, "raw_constraints": raw_constraints,
            "indexes": indexes, "paths": paths}


def catalog_constraint_definition(definition: str, table: str, column_names: set[str]) -> str:
    """DSQL exposes its automatic primary-index coverage in the PK constraint.

    Accept only the documented full non-key coverage, without weakening the
    primary key itself or accepting INCLUDE on unrelated constraint types.
    """
    match = re.fullmatch(r"(PRIMARY KEY\s*\(([^)]+)\))\s+INCLUDE\s*\(([^)]+)\)",
                         definition, re.IGNORECASE)
    if match:
        keys = [part.strip() for part in match[2].split(",")]
        includes = [part.strip() for part in match[3].split(",")]
        if (any(not re.fullmatch(r"[a-z_][a-z_0-9]*", name) for name in keys + includes)
                or not set(keys) <= column_names or len(keys) != len(set(keys))
                or len(includes) != len(set(includes))
                or set(includes) != column_names - set(keys)):
            raise BootstrapError(f"Unexpected primary-key INCLUDE columns on {table}")
        definition = match[1]
    return normalized_definition(definition)


def verify_schema(connection: Any, sql_dir: Path = SQL_DIR) -> dict[str, int]:
    expected = expected_schema(sql_dir)
    rows = connection.execute("""SELECT table_name, column_name, data_type,
        character_maximum_length, datetime_precision, is_nullable, column_default, ordinal_position
        FROM information_schema.columns WHERE table_schema = 'public'
        AND table_name IN ('alarms','alarm_events','incidents') ORDER BY table_name, ordinal_position""").fetchall()
    actual_columns, attribute_names = {}, {}
    for row in rows:
        attribute_names.setdefault(row["table_name"], {})[row["ordinal_position"]] = row["column_name"]
        actual_columns.setdefault(row["table_name"], []).append((
            row["column_name"], row["data_type"], row["character_maximum_length"],
            row["datetime_precision"], row["is_nullable"], normalized_definition(row["column_default"])))
    if actual_columns != expected["columns"]:
        raise BootstrapError("Column types, order, nullability or defaults differ from approved SQL")
    rows = connection.execute("""SELECT t.relname AS table_name, c.conname AS name,
        pg_catalog.pg_get_constraintdef(c.oid) AS definition, c.convalidated AS valid
        FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class t ON t.oid=c.conrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND t.relname IN ('alarms','alarm_events','incidents')
        AND c.contype IN ('p','u','f','c')""").fetchall()
    actual_constraints = {(row["table_name"], row["name"]): catalog_constraint_definition(
        row["definition"], row["table_name"], set(attribute_names[row["table_name"]].values())) for row in rows}
    if actual_constraints != expected["constraints"] or any(not row["valid"] for row in rows):
        raise BootstrapError("PK, UNIQUE, FK or CHECK definitions differ from approved SQL")
    rows = connection.execute("""SELECT t.relname AS table_name, c.relname AS name,
        i.indisvalid AS valid, i.indisready AS ready, i.indisunique AS unique_index,
        i.indisprimary AS primary_index, i.indnkeyatts AS key_count,
        i.indnatts AS attribute_count, i.indkey::text AS attribute_numbers,
        i.indoption::text AS options, am.amname AS method,
        pg_catalog.pg_get_expr(i.indpred,i.indrelid) AS predicate,
        pg_catalog.pg_get_expr(i.indexprs,i.indrelid) AS expressions
        FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
        JOIN pg_catalog.pg_class t ON t.oid=i.indrelid
        JOIN pg_catalog.pg_am am ON am.oid=c.relam
        JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND t.relname IN ('alarms','alarm_events','incidents')""").fetchall()
    actual_indexes = {}
    for row in rows:
        table = row["table_name"]
        numbers = [int(x) for x in row["attribute_numbers"].split()]
        options = [int(x) for x in row["options"].split()]
        names = attribute_names[table]
        if (not row["valid"] or not row["ready"] or row["expressions"] is not None
                or row["method"] not in {"btree", "btree_index"}
                or len(numbers) != row["attribute_count"] or len(set(numbers)) != len(numbers)
                or not 0 < row["key_count"] <= len(numbers) or any(n not in names for n in numbers)
                or len(options) != row["key_count"] or any(options)):
            raise BootstrapError("Index is invalid, not ready or has unexpected key structure")
        keys = tuple(names[n] for n in numbers[:row["key_count"]])
        includes = tuple(names[n] for n in numbers[row["key_count"]:])
        # DSQL automatically covers all remaining columns in primary indexes.
        # Ordinary PostgreSQL does not; both representations express this schema.
        if includes and (not row["primary_index"] or set(includes) != set(names.values()) - set(keys)):
            raise BootstrapError("Unexpected included columns in schema index")
        actual_indexes[(table, row["name"])] = {"keys": keys, "unique": row["unique_index"],
            "primary": row["primary_index"],
            "predicate": normalized_expression(row["predicate"]) if row["predicate"] else None}
    if actual_indexes != expected["indexes"]:
        raise BootstrapError("Index keys, uniqueness or predicates differ from approved SQL")
    return {"tables": len(actual_columns), "columns": sum(map(len, actual_columns.values())),
            "constraints": len(actual_constraints), "indexes": len(actual_indexes)}


def wait_for_index(connection: Any, job_id: str, timeout: int, *, clock=time.monotonic, sleep=time.sleep) -> None:
    deadline = clock() + timeout
    while True:
        row = connection.execute("SELECT status FROM sys.jobs WHERE job_id = %s", (job_id,)).fetchone()
        if row is None:
            raise BootstrapError("Index job is missing; inspect its catalog state before retrying")
        if row["status"] == "completed":
            return
        if row["status"] not in {"submitted", "processing"}:
            raise BootstrapError("Index job did not complete successfully; inspect sys.jobs")
        if clock() >= deadline:
            raise BootstrapError("Index job timed out; it may still be running, so do not recreate blindly")
        sleep(min(1, max(0, deadline - clock())))


def initialize_schema(connection: Any, timeout: int = 300, sql_dir: Path = SQL_DIR) -> dict[str, int]:
    existing = connection.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'").fetchall()
    if existing:
        raise BootstrapError("Schema initialization requires an empty public schema; inspect existing or partial objects")
    expected = expected_schema(sql_dir)
    for path in expected["paths"]:
        print(json.dumps({"step": "schema", "file": path.name}), flush=True)
        cursor = connection.execute(path.read_text())
        if int(path.name[:3]) >= 4:
            row = cursor.fetchone()
            if not row or not isinstance(row.get("job_id"), str) or not row["job_id"]:
                raise BootstrapError("CREATE INDEX ASYNC did not return a job_id")
            wait_for_index(connection, row["job_id"], timeout)
    return {"completed_schema_files": 7}


def expected_permissions(config: Config) -> dict[tuple[str, str], set[str]]:
    result = {}
    for table in TABLES:
        result[(config.read_db_role, table)] = {"SELECT"}
        result[(config.ingest_db_role, table)] = {"SELECT", "INSERT"} | ({"UPDATE"} if table != "alarm_events" else set())
    return result


def verify_public_schema_permissions(connection: Any, config: Config) -> None:
    for role in (config.ingest_db_role, config.read_db_role):
        row = connection.execute("""SELECT has_schema_privilege(%s,'public','USAGE') AS usage,
            has_schema_privilege(%s,'public','CREATE') AS create_allowed""", (role, role)).fetchone()
        if not row["usage"] or row["create_allowed"]:
            raise BootstrapError("Runtime schema privileges must allow USAGE but not CREATE")
        grantable = connection.execute("SELECT has_schema_privilege(%s,'public','USAGE WITH GRANT OPTION') AS allowed",
                                       (role,)).fetchone()
        if grantable["allowed"]:
            raise BootstrapError("Runtime roles must not delegate schema privileges")


def verify_roles(connection: Any, config: Config) -> dict[str, Any]:
    roles = (config.ingest_db_role, config.read_db_role)
    rows = connection.execute("""SELECT rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb,
        rolreplication, rolbypassrls FROM pg_catalog.pg_roles WHERE rolname IN (%s,%s)""", roles).fetchall()
    if {row["rolname"] for row in rows} != set(roles) or any(
        not row["rolcanlogin"] or any(row[key] for key in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls"))
        for row in rows
    ):
        raise BootstrapError("Runtime database role login or administrative attributes are incorrect")
    memberships = connection.execute("""SELECT r.rolname FROM pg_catalog.pg_auth_members m
        JOIN pg_catalog.pg_roles r ON r.oid=m.member WHERE r.rolname IN (%s,%s)""", roles).fetchall()
    if memberships:
        raise BootstrapError("Runtime database roles have unexpected inherited role memberships")
    mappings = connection.execute("""SELECT arn, pg_role_name FROM sys.iam_pg_role_mappings
        WHERE arn IN (%s,%s) OR pg_role_name IN (%s,%s)""",
        (config.ingest_iam_role_arn, config.read_iam_role_arn, *roles)).fetchall()
    expected = {(config.ingest_iam_role_arn, config.ingest_db_role), (config.read_iam_role_arn, config.read_db_role)}
    if {(row["arn"], row["pg_role_name"]) for row in mappings} != expected:
        raise BootstrapError("IAM/database mappings must be exactly the two separate runtime pairs")
    verify_public_schema_permissions(connection, config)
    for (role, table), allowed in expected_permissions(config).items():
        for privilege in TABLE_PRIVILEGES:
            row = connection.execute("SELECT has_table_privilege(%s,%s,%s) AS allowed",
                                     (role, "public." + table, privilege)).fetchone()
            if row["allowed"] != (privilege in allowed):
                raise BootstrapError(f"Unexpected {privilege} privilege for {role} on {table}")
            grantable = connection.execute("SELECT has_table_privilege(%s,%s,%s) AS allowed",
                (role, "public." + table, privilege + " WITH GRANT OPTION")).fetchone()
            if grantable["allowed"]:
                raise BootstrapError("Runtime roles must not delegate table privileges")
    return {"runtime_roles": 2, "iam_mappings": 2, "privileges": "matched"}


def initialize_roles(connection: Any, config: Config) -> dict[str, Any]:
    verify_schema(connection)
    tables = connection.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'").fetchall()
    if {row["tablename"] for row in tables} != set(TABLES):
        raise BootstrapError("Role initialization is restricted to the dedicated three-table public schema")
    roles = connection.execute("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname IN (%s,%s)",
                               (config.ingest_db_role, config.read_db_role)).fetchall()
    if roles:
        # A completely configured rerun is harmless; partial or unexpected roles
        # require manual review instead of silently changing an existing identity.
        return verify_roles(connection, config)
    mappings = connection.execute("SELECT arn FROM sys.iam_pg_role_mappings WHERE arn IN (%s,%s)",
                                  (config.ingest_iam_role_arn, config.read_iam_role_arn)).fetchall()
    if mappings:
        raise BootstrapError("A runtime IAM role already has a database mapping; inspect before setup")
    for number, statement in enumerate(role_statements(config), 1):
        print(json.dumps({"step": "roles", "statement": number}), flush=True)
        connection.execute(statement)
        if number == 2:
            verify_public_schema_permissions(connection, config)
    return {"created_runtime_roles": 2}


def connect_admin(config: Config, profile: str | None = None) -> Any:
    import boto3
    import certifi
    import psycopg
    from psycopg.rows import dict_row

    session = boto3.Session(profile_name=profile, region_name=config.region)
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != config.account_id:
        raise BootstrapError("Current AWS identity is not in the configured deployment account")
    client = session.client("dsql")
    cluster = client.get_cluster(identifier=config.cluster_id)
    if (cluster.get("arn"), cluster.get("endpoint"), cluster.get("status")) != (
        config.cluster_arn, config.endpoint, "ACTIVE"
    ):
        raise BootstrapError("DSQL cluster identity, endpoint or ACTIVE status does not match configuration")
    token = client.generate_db_connect_admin_auth_token(Hostname=config.endpoint, Region=config.region, ExpiresIn=900)
    return psycopg.connect(host=config.endpoint, port=5432, dbname="postgres", user="admin", password=token,
                           sslmode="verify-full", sslrootcert=certifi.where(), connect_timeout=10,
                           autocommit=True, row_factory=dict_row, prepare_threshold=None,
                           application_name="lambdaops-dsql-bootstrap", options="-c search_path=public")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preview", "schema", "roles", "verify"))
    parser.add_argument("--config", required=True, type=Path, help="terraform output -json dsql_bootstrap")
    parser.add_argument("--profile", help="Optional local AWS profile for the operator, never a Lambda role")
    parser.add_argument("--index-timeout", type=int, default=300, help="Polling deadline for each ASYNC index")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.index_timeout <= 3600:
            raise BootstrapError("Index timeout must be between 1 and 3600 seconds")
        config = Config.parse(json.loads(args.config.read_text()))
        if args.command == "preview":
            expected = expected_schema()
            print(json.dumps({"cluster_arn": config.cluster_arn, "endpoint": config.endpoint,
                "schema_files": [path.name for path in expected["paths"]],
                "role_statements": role_statements(config), "aws_contacted": False}, indent=2))
            return 0
        with connect_admin(config, args.profile) as connection:
            if args.command == "schema":
                result = initialize_schema(connection, args.index_timeout)
            elif args.command == "roles":
                result = initialize_roles(connection, config)
            else:
                result = {**verify_schema(connection), **verify_roles(connection, config)}
        # DSQL schema and permission caches can survive changes within a session.
        # Inspect committed objects using a fresh admin connection after writes.
        if args.command in {"schema", "roles"}:
            with connect_admin(config, args.profile) as connection:
                result.update(verify_schema(connection))
                if args.command == "roles":
                    result.update(verify_roles(connection, config))
        print(json.dumps({"command": args.command, "result": result,
                          "runtime_lambda_connection_verified": False}, sort_keys=True))
        return 0
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        # Driver errors can embed credentials or SQL. Report only safe identifiers.
        print(f"Bootstrap failed: {type(exc).__name__}; SQLSTATE={getattr(exc, 'sqlstate', None)}. "
              "Inspect current objects before retrying; earlier DDL may be committed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
