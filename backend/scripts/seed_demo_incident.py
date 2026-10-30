"""Preview or insert one clearly marked, synthetic Incident for a read-only demo.

This is an administrator-only, direct DSQL seed. It does not create a CloudWatch
Alarm, send an EventBridge event, or exercise the ingest Lambda. Preview uses
only Terraform output; --apply explicitly authorizes one DSQL write attempt.

Run preview first and reuse its seed_id and transitioned_at for --apply. The
operator must also confirm the expected account, region, and cluster on apply.
No credentials, IAM tokens, or raw database errors are printed or saved.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
from uuid import UUID, uuid4


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
for import_dir in (SCRIPT_DIR, BACKEND_DIR):
    if str(import_dir) not in sys.path:
        sys.path.insert(0, str(import_dir))

from bootstrap_dsql import BootstrapError, Config, connect_admin
from lambdaops_m3.contracts import parse_eventbridge_alarm
from lambdaops_m3.dsql_store import DsqlIncidentStore


SERVICE = "slash-api"
ENVIRONMENT = "dev"
PROVENANCE = "DEMO: direct administrator DSQL seed; not a CloudWatch or EventBridge delivery"


def load_config(terraform_root: Path) -> Config:
    """Read only the named, nonsecret Terraform output; never print its raw text."""
    if not terraform_root.is_dir():
        raise BootstrapError("--terraform-root must name an existing directory")
    try:
        result = subprocess.run(
            ["terraform", "output", "-json", "dsql_bootstrap"],
            cwd=terraform_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return Config.parse(json.loads(result.stdout))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise BootstrapError("Could not read the dsql_bootstrap Terraform output") from exc


def parse_seed_id(value: str) -> UUID:
    try:
        result = UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--seed-id must be a UUID version 4") from exc
    if result.version != 4 or str(result) != value.lower():
        raise argparse.ArgumentTypeError("--seed-id must be a canonical UUID version 4")
    return result


def parse_transitioned_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--transitioned-at must be an ISO timestamp with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--transitioned-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def build_demo_event(config: Config, seed_id: UUID, transitioned_at: datetime) -> dict[str, Any]:
    """Make a unique ALARM transition with visible synthetic provenance."""
    alarm_name = f"DEMO-lambdaops-read-{seed_id}"
    alarm_arn = f"arn:aws:cloudwatch:{config.region}:{config.account_id}:alarm:{alarm_name}"
    event_id = f"DEMO-{seed_id}"
    return {
        "version": "0",
        "id": event_id,
        "detail-type": "CloudWatch Alarm State Change",
        "source": "aws.cloudwatch",
        "account": config.account_id,
        "time": _timestamp(transitioned_at),
        "region": config.region,
        "resources": [alarm_arn],
        "lambdaopsDemoProvenance": PROVENANCE,
        "detail": {
            "alarmName": alarm_name,
            "previousState": {
                "value": "OK",
                "reason": PROVENANCE,
                "timestamp": _timestamp(transitioned_at - timedelta(seconds=1)),
            },
            "state": {
                "value": "ALARM",
                "reason": PROVENANCE,
                "timestamp": _timestamp(transitioned_at),
            },
        },
    }


def _connection_factory(config: Config, profile: str | None):
    # connect_admin checks the STS account and the exact ACTIVE DSQL cluster,
    # but initially returns an autocommit connection for DDL. IncidentStore
    # requires a single repeatable-read transaction for its whole projection.
    import psycopg

    connection = connect_admin(config, profile)
    try:
        connection.autocommit = False
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    except Exception:
        connection.close()
        raise
    return connection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terraform-root", required=True, type=Path,
                        help="Explicit deployed Terraform root containing dsql_bootstrap output")
    parser.add_argument("--seed-id", type=parse_seed_id,
                        help="UUID v4 from preview; required for --apply")
    parser.add_argument("--transitioned-at", type=parse_transitioned_at,
                        help="UTC transition timestamp from preview; required for --apply")
    parser.add_argument("--apply", action="store_true", help="Write the synthetic Incident to DSQL")
    parser.add_argument("--expected-account-id", help="Confirm the intended 12-digit AWS account for --apply")
    parser.add_argument("--expected-region", help="Confirm the intended AWS region for --apply")
    parser.add_argument("--expected-cluster-id", help="Confirm the intended DSQL cluster ID for --apply")
    parser.add_argument("--profile", help="Optional operator AWS profile used for DSQL admin connection")
    args = parser.parse_args(argv)

    if args.apply and (args.seed_id is None or args.transitioned_at is None or
                       not args.expected_account_id or not args.expected_region or
                       not args.expected_cluster_id):
        parser.error("--apply requires --seed-id, --transitioned-at, --expected-account-id, --expected-region, and --expected-cluster-id")

    try:
        config = load_config(args.terraform_root)
        if args.apply and (
            args.expected_account_id != config.account_id or
            args.expected_region != config.region or
            args.expected_cluster_id != config.cluster_id
        ):
            raise BootstrapError("The confirmed account, region or cluster does not match Terraform output")
        seed_id = args.seed_id or uuid4()
        transitioned_at = args.transitioned_at or datetime.now(timezone.utc)
        event = build_demo_event(config, seed_id, transitioned_at)
        transition = parse_eventbridge_alarm(
            event, {event["resources"][0]}, SERVICE, ENVIRONMENT
        )
        result: dict[str, Any] = {
            "mode": "apply" if args.apply else "preview",
            "provenance": PROVENANCE,
            "account_id": config.account_id,
            "region": config.region,
            "cluster_id": config.cluster_id,
            "service": SERVICE,
            "environment": ENVIRONMENT,
            "seed_id": str(seed_id),
            "transitioned_at": transition.transitioned_at,
            "event_id": transition.event_id,
            "alarm_arn": transition.alarm_arn,
        }
        if args.apply:
            applied = DsqlIncidentStore(
                lambda: _connection_factory(config, args.profile)
            ).apply(transition, event)
            result.update({"outcome": applied.outcome, "incident_id": applied.incident_id,
                           "aws_write": applied.outcome != "duplicate"})
        else:
            result["aws_write"] = False
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except BootstrapError as exc:
        print(f"seed_demo_incident: {exc}", file=sys.stderr)
        return 2
    except Exception:
        # A raw psycopg/boto3 error can contain a connection string or token.
        print("seed_demo_incident: seed not confirmed; inspect local AWS/DSQL access and logs", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
