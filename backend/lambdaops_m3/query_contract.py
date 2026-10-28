"""Validated parameters for the DSQL Incident page API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping, Optional

from .contracts import ALARM_STATES


class QueryContractError(ValueError):
    """A caller supplied an unsupported or invalid query parameter."""


_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_PARAMS = frozenset({
    "page", "pageSize", "service", "environment", "alarmState", "incidentState",
    "from", "to", "search", "sortBy", "sortOrder",
})
SORT_COLUMNS = {
    "detectedAt": "i.detected_at",
    "lastStateTransitionAt": "i.last_state_transition_at",
    "createdAt": "i.created_at",
}


def _integer(params: Mapping[str, Any], name: str, default: int, maximum: int) -> int:
    if name not in params:
        return default
    value = params[name]
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,7}", value):
        raise QueryContractError("Invalid " + name)
    result = int(value)
    if not 1 <= result <= maximum:
        raise QueryContractError("Invalid " + name)
    return result


def _text(params: Mapping[str, Any], name: str, maximum: int) -> Optional[str]:
    if name not in params:
        return None
    value = params[name]
    if (not isinstance(value, str) or not value.strip()
            or len(value) > maximum or "\x00" in value):
        raise QueryContractError("Invalid " + name)
    return value


def _time(params: Mapping[str, Any], name: str) -> Optional[str]:
    if name not in params:
        return None
    value = params[name]
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise QueryContractError("Invalid " + name)
    # Python normalizes offsets such as +01:60; the API accepts valid RFC 3339
    # offsets only. Keep this validation independent from that normalization.
    if not value.endswith("Z") and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise QueryContractError("Invalid " + name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError) as exc:
        raise QueryContractError("Invalid " + name) from exc


@dataclass(frozen=True)
class IncidentQuery:
    page: int = 1
    page_size: int = 50
    service: Optional[str] = None
    environment: Optional[str] = None
    alarm_state: Optional[str] = None
    incident_state: Optional[str] = None
    from_at: Optional[str] = None
    to_at: Optional[str] = None
    search: Optional[str] = None
    sort_by: str = "detectedAt"
    sort_order: str = "desc"

    @classmethod
    def parse(cls, params: Optional[Mapping[str, Any]]) -> "IncidentQuery":
        return parse_incident_query(params)


def parse_incident_query(params: Optional[Mapping[str, Any]]) -> IncidentQuery:
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        raise QueryContractError("Invalid query parameters")
    unknown = set(params) - _PARAMS
    if unknown:
        raise QueryContractError("Unsupported query parameter")
    page = _integer(params, "page", 1, 1_000_000)
    page_size = _integer(params, "pageSize", 50, 100)
    service = _text(params, "service", 100)
    environment = _text(params, "environment", 32)
    alarm_state = _text(params, "alarmState", 20)
    incident_state = _text(params, "incidentState", 20)
    if alarm_state is not None and alarm_state not in ALARM_STATES:
        raise QueryContractError("Invalid alarmState")
    if incident_state is not None and incident_state != "NEW":
        raise QueryContractError("Invalid incidentState")
    from_at, to_at = _time(params, "from"), _time(params, "to")
    if from_at is not None and to_at is not None and from_at >= to_at:
        raise QueryContractError("Invalid time range")
    search = _text(params, "search", 200)
    sort_by = params.get("sortBy", "detectedAt")
    sort_order = params.get("sortOrder", "desc")
    if not isinstance(sort_by, str) or sort_by not in SORT_COLUMNS:
        raise QueryContractError("Invalid sortBy")
    if not isinstance(sort_order, str) or sort_order not in {"asc", "desc"}:
        raise QueryContractError("Invalid sortOrder")
    return IncidentQuery(page, page_size, service, environment, alarm_state,
                         incident_state, from_at, to_at, search, sort_by, sort_order)
