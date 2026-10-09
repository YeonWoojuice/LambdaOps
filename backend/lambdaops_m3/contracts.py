"""Internal contract for CloudWatch alarm state changes carried by EventBridge.

The public AWS envelope is validated at this boundary. Processing code receives a
canonical transition timestamp so string ordering and the dedupe key use the same
instant even when AWS supplies different UTC offsets.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Dict, Optional, Set


ALARM_STATES = frozenset({"ALARM", "OK", "INSUFFICIENT_DATA"})
_ALARM_ARN = re.compile(
    r"^arn:[^:]+:cloudwatch:(?P<region>[^:]+):(?P<account>\d{12}):alarm:(?P<name>.+)$"
)
# CloudWatch examples use +0000; RFC 3339 uses +00:00. Accept both forms.
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})$"
)


class EventContractError(ValueError):
    """An EventBridge payload does not satisfy the M3 alarm contract."""


@dataclass(frozen=True)
class AlarmTransition:
    event_id: str
    account: str
    region: str
    alarm_arn: str
    alarm_name: str
    state: str
    transitioned_at: str
    service: str
    environment: str
    dedupe_key: str
    previous_state: Optional[str] = None
    previous_transitioned_at: Optional[str] = None

    @property
    def raw_event_ref(self) -> str:
        """Reference to the original EventBridge envelope for evidence lookup."""
        return self.event_id


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventContractError("%s must be a nonempty string" % field)
    return value


def _canonical_utc_timestamp(value: Any) -> str:
    timestamp = _nonempty_string(value, "detail.state.timestamp")
    if not _TIMESTAMP.fullmatch(timestamp):
        raise EventContractError("detail.state.timestamp must include a timezone")
    if timestamp.endswith("Z"):
        timestamp = timestamp[:-1] + "+00:00"
    elif timestamp[-3] != ":":
        timestamp = timestamp[:-2] + ":" + timestamp[-2:]
    try:
        parsed = datetime.fromisoformat(timestamp)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise EventContractError("detail.state.timestamp is invalid") from exc


def parse_eventbridge_alarm(
    event: Dict[str, Any],
    allowed_alarm_arns: Set[str],
    service: str,
    environment: str,
) -> AlarmTransition:
    """Validate one CloudWatch Alarm State Change event and normalize its identity.

    ``allowed_alarm_arns`` comes from the configured M2 alarm outputs. It prevents
    an unrelated alarm in the account from becoming a LambdaOps Incident.
    """
    if not isinstance(event, dict):
        raise EventContractError("event must be an object")
    if event.get("source") != "aws.cloudwatch":
        raise EventContractError("source must be aws.cloudwatch")
    if event.get("detail-type") != "CloudWatch Alarm State Change":
        raise EventContractError("detail-type must be CloudWatch Alarm State Change")

    event_id = _nonempty_string(event.get("id"), "id")
    account = _nonempty_string(event.get("account"), "account")
    region = _nonempty_string(event.get("region"), "region")
    service = _nonempty_string(service, "service")
    environment = _nonempty_string(environment, "environment")

    resources = event.get("resources")
    if not isinstance(resources, list) or len(resources) != 1:
        raise EventContractError("resources must contain one alarm ARN")
    alarm_arn = _nonempty_string(resources[0], "resources[0]")
    arn_parts = _ALARM_ARN.fullmatch(alarm_arn)
    if arn_parts is None:
        raise EventContractError("resources[0] must be a CloudWatch alarm ARN")
    if arn_parts.group("account") != account or arn_parts.group("region") != region:
        raise EventContractError("account and region must match the alarm ARN")
    if alarm_arn not in allowed_alarm_arns:
        raise EventContractError("alarm ARN is not configured for M3 ingestion")

    detail = event.get("detail")
    if not isinstance(detail, dict):
        raise EventContractError("detail must be an object")
    alarm_name = _nonempty_string(detail.get("alarmName"), "detail.alarmName")
    if alarm_name != arn_parts.group("name"):
        raise EventContractError("detail.alarmName must match the alarm ARN")
    state_detail = detail.get("state")
    if not isinstance(state_detail, dict):
        raise EventContractError("detail.state must be an object")
    state = state_detail.get("value")
    if not isinstance(state, str) or state not in ALARM_STATES:
        raise EventContractError("detail.state.value is unsupported")
    transitioned_at = _canonical_utc_timestamp(state_detail.get("timestamp"))

    previous_detail = detail.get("previousState")
    if not isinstance(previous_detail, dict):
        raise EventContractError("detail.previousState must be an object")
    previous_state = previous_detail.get("value")
    if previous_state not in ALARM_STATES:
        raise EventContractError("detail.previousState.value is unsupported")
    previous_transitioned_at = _canonical_utc_timestamp(previous_detail.get("timestamp"))
    if previous_transitioned_at >= transitioned_at:
        raise EventContractError("previous state must predate the new state")

    identity = json.dumps(
        [account, region, alarm_arn, state, transitioned_at],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    dedupe_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return AlarmTransition(
        event_id=event_id,
        account=account,
        region=region,
        alarm_arn=alarm_arn,
        alarm_name=alarm_name,
        state=state,
        transitioned_at=transitioned_at,
        service=service,
        environment=environment,
        dedupe_key=dedupe_key,
        previous_state=previous_state,
        previous_transitioned_at=previous_transitioned_at,
    )
