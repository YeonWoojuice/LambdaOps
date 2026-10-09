"""Append-only alarm evidence and transactional Incident projections.

Every valid transition is retained, including one delivered after a newer
transition. A per-alarm cursor version serializes the read/reproject/write
cycle, so the projection converges regardless of SQS arrival order.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .contracts import AlarmTransition, parse_eventbridge_alarm
from .projection import Episode, ProjectionConflict, project_episodes


MAX_TRANSACTION_ACTIONS = 100
MAX_TRANSACTION_BYTES = 4 * 1024 * 1024
MAX_ITEM_BYTES = 400 * 1024


class TransitionConflict(ProjectionConflict):
    """Stored and incoming alarm transitions describe incompatible evidence."""


class ProjectionTooLarge(RuntimeError):
    """A projection cannot be published in one DynamoDB transaction."""


@dataclass(frozen=True)
class ApplyResult:
    outcome: str  # created, reconstructed, updated, recorded, or duplicate
    incident_id: Optional[str] = None


def alarm_key_from_parts(account: str, region: str, alarm_arn: str) -> str:
    encoded = json.dumps([account, region, alarm_arn], separators=(",", ":"))
    return "ALARM#" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def alarm_key(transition: AlarmTransition) -> str:
    return alarm_key_from_parts(transition.account, transition.region, transition.alarm_arn)


def incident_id_for(transition: AlarmTransition) -> str:
    encoded = json.dumps(
        [transition.account, transition.region, transition.alarm_arn, transition.transitioned_at],
        separators=(",", ":"),
    )
    return "inc_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _event_record(transition: AlarmTransition, raw_event: Dict[str, Any]) -> Dict[str, str]:
    return {
        "PK": alarm_key(transition),
        "SK": "EVENT#" + transition.transitioned_at + "#" + transition.dedupe_key,
        "eventId": transition.event_id,
        "state": transition.state,
        "transitionedAt": transition.transitioned_at,
        "dedupeKey": transition.dedupe_key,
        "service": transition.service,
        "environment": transition.environment,
        "rawEvent": json.dumps(raw_event, separators=(",", ":"), ensure_ascii=False),
    }


def _public_event(event: Dict[str, str]) -> Dict[str, Any]:
    return {
        "eventId": event["eventId"],
        "state": event["state"],
        "transitionedAt": event["transitionedAt"],
        "rawEvent": json.loads(event["rawEvent"]),
    }


def _incident_meta(episode: Episode) -> Dict[str, str]:
    first = episode.alarm_events[0]
    incident_id = incident_id_for(first)
    item = {
        "incidentId": incident_id,
        "account": first.account,
        "region": first.region,
        "service": first.service,
        "environment": first.environment,
        "alarmArn": first.alarm_arn,
        "alarmName": first.alarm_name,
        "detectedAt": episode.start_at,
        "alarmState": episode.last_state,
        "incidentState": "NEW",
        "lastStateTransitionAt": episode.last_at,
        "originalEventRef": first.event_id,
        "GSI1PK": "INCIDENTS",
        "GSI1SK": episode.start_at + "#" + incident_id,
    }
    if episode.alarm_ok_at is not None:
        item["alarmOkAt"] = episode.alarm_ok_at
    return item


def _desired(transitions: List[AlarmTransition], key: str) -> Dict[str, Dict[str, Any]]:
    try:
        episodes = project_episodes(transitions)
    except ProjectionConflict as exc:
        raise TransitionConflict(str(exc)) from exc
    desired: Dict[str, Dict[str, Any]] = {}
    for episode in episodes:
        # A synthetic ALARM is evidence of an opening transition, but does not
        # carry the original EventBridge event needed for an Incident record.
        if not episode.alarm_events:
            continue
        first = episode.alarm_events[0]
        incident_id = incident_id_for(first)
        meta = _incident_meta(episode)
        digest = hashlib.sha256(
            json.dumps(meta, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if incident_id in desired:
            raise TransitionConflict("one ALARM anchors two projected episodes")
        desired[incident_id] = {
            "meta": meta,
            "index": {
                "PK": key,
                "SK": "PROJ#" + incident_id,
                "incidentId": incident_id,
                "anchorAt": first.transitioned_at,
                "projectionHash": digest,
            },
            "episode": episode,
        }
    return desired


def _alias_targets(old_index: Dict[str, Dict[str, str]], desired: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    alarm_to_new_id = {
        event.transitioned_at: incident_id
        for incident_id, projected in desired.items()
        for event in projected["episode"].alarm_events
    }
    aliases = {}
    for old_id in old_index.keys() - desired.keys():
        target = alarm_to_new_id.get(old_index[old_id]["anchorAt"])
        if not target:
            raise TransitionConflict("a published ALARM disappeared from the event ledger")
        aliases[old_id] = target
    return aliases


def _result_for(
    transition: AlarmTransition,
    old_index: Dict[str, Dict[str, str]],
    desired: Dict[str, Dict[str, Any]],
) -> ApplyResult:
    for incident_id, projected in desired.items():
        episode = projected["episode"]
        if any(event.dedupe_key == transition.dedupe_key for event in episode.events):
            if incident_id not in old_index:
                outcome = "created" if transition.state == "ALARM" and episode.last_at == transition.transitioned_at else "reconstructed"
            else:
                outcome = "updated"
            return ApplyResult(outcome, incident_id)
    return ApplyResult("recorded")


def _cursor_record(key: str, version: int, transitions: List[AlarmTransition]) -> Dict[str, str]:
    latest = max(transitions, key=lambda item: item.transitioned_at)
    return {
        "PK": key,
        "SK": "STATE",
        "version": str(version),
        "latestTransitionAt": latest.transitioned_at,
        "latestState": latest.state,
    }


def _av_string(value: str) -> Dict[str, str]:
    return {"S": value}


def _serialize(item: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    return {name: _av_string(str(value)) for name, value in item.items() if value is not None}


def _deserialize(item: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    return {
        name: value["S"] if "S" in value else value["N"]
        for name, value in item.items()
        if "S" in value or "N" in value
    }


def _check_transaction(writes: List[Dict[str, Any]]) -> None:
    if len(writes) > MAX_TRANSACTION_ACTIONS:
        raise ProjectionTooLarge("More than 100 DynamoDB transaction actions are required")
    for operation in writes:
        put = operation.get("Put")
        if put is not None:
            encoded = json.dumps(put["Item"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_ITEM_BYTES:
                raise ProjectionTooLarge("A DynamoDB item exceeds the 400 KiB safety bound")
    encoded_writes = json.dumps(writes, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded_writes) > MAX_TRANSACTION_BYTES:
        raise ProjectionTooLarge("A DynamoDB transaction exceeds the 4 MiB safety bound")


def _public_meta(item: Dict[str, str]) -> Dict[str, str]:
    return {name: value for name, value in item.items() if name not in ("PK", "SK", "GSI1PK", "GSI1SK")}


class MemoryIncidentStore:
    """Atomic in-memory equivalent of the immutable ledger and projection."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: Dict[str, Dict[str, Dict[str, str]]] = {}
        self._incidents: Dict[str, Dict[str, str]] = {}
        self._index: Dict[str, Dict[str, Dict[str, str]]] = {}
        self._versions: Dict[str, int] = {}

    def apply(self, transition: AlarmTransition, raw_event: Dict[str, Any]) -> ApplyResult:
        key = alarm_key(transition)
        with self._lock:
            records = self._events.get(key, {})
            if transition.dedupe_key in records:
                return ApplyResult("duplicate")
            existing = [
                parse_eventbridge_alarm(
                    json.loads(record["rawEvent"]),
                    {transition.alarm_arn},
                    record.get("service", transition.service),
                    record.get("environment", transition.environment),
                )
                for record in records.values()
            ]
            all_transitions = existing + [transition]
            desired = _desired(all_transitions, key)
            old_index = self._index.get(key, {})
            aliases = _alias_targets(old_index, desired)
            result = _result_for(transition, old_index, desired)
            self._events.setdefault(key, {})[transition.dedupe_key] = _event_record(transition, raw_event)
            for incident_id, projected in desired.items():
                if old_index.get(incident_id, {}).get("projectionHash") != projected["index"]["projectionHash"]:
                    self._incidents[incident_id] = dict(projected["meta"])
            for old_id, new_id in aliases.items():
                self._incidents[old_id] = {"aliasTo": new_id}
            self._index[key] = {incident_id: dict(projected["index"]) for incident_id, projected in desired.items()}
            self._versions[key] = self._versions.get(key, 0) + 1
            return result

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._incidents.get(incident_id)
            seen = set()
            while item is not None and "aliasTo" in item:
                if incident_id in seen:
                    raise RuntimeError("Incident alias cycle")
                seen.add(incident_id)
                incident_id = item["aliasTo"]
                item = self._incidents.get(incident_id)
            if item is None:
                return None
            key = alarm_key_from_parts(item["account"], item["region"], item["alarmArn"])
            raw_by_key = self._events.get(key, {})
            transitions = [
                parse_eventbridge_alarm(
                    json.loads(record["rawEvent"]),
                    {item["alarmArn"]},
                    record.get("service", item["service"]),
                    record.get("environment", item["environment"]),
                )
                for record in raw_by_key.values()
            ]
            projected = _desired(transitions, key).get(incident_id)
            if projected is None:
                raise RuntimeError("Incident projection is missing")
            result = _public_meta(item)
            result["stateHistory"] = [
                _public_event(raw_by_key[event.dedupe_key])
                for event in projected["episode"].events
            ]
            return result

    def list_incidents(self, limit: int = 50, next_token: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if next_token:
            raise ValueError("MemoryIncidentStore does not support pagination tokens")
        with self._lock:
            current = [item for item in self._incidents.values() if item.get("GSI1PK") == "INCIDENTS"]
            current.sort(key=lambda item: item["GSI1SK"], reverse=True)
            return [_public_meta(item) for item in current[:limit]], None


class DynamoIncidentStore:
    """DynamoDB adapter with one transaction per accepted transition."""

    def __init__(self, table_name: str, client: Any = None) -> None:
        if not table_name:
            raise ValueError("INCIDENTS_TABLE_NAME is required")
        if client is None:
            import boto3  # Lambda runtime dependency; local tests inject a fake client.

            client = boto3.client("dynamodb")
        self.table_name = table_name
        self.client = client

    def _get_item(self, pk: str, sk: str) -> Optional[Dict[str, str]]:
        response = self.client.get_item(
            TableName=self.table_name,
            Key={"PK": _av_string(pk), "SK": _av_string(sk)},
            ConsistentRead=True,
        )
        return _deserialize(response["Item"]) if response.get("Item") else None

    def _query_prefix(self, pk: str, prefix: str) -> List[Dict[str, str]]:
        items = []
        start_key = None
        while True:
            args: Dict[str, Any] = {
                "TableName": self.table_name,
                "KeyConditionExpression": "PK = :pk AND begins_with(SK, :prefix)",
                "ExpressionAttributeValues": {":pk": _av_string(pk), ":prefix": _av_string(prefix)},
                "ConsistentRead": True,
            }
            if start_key:
                args["ExclusiveStartKey"] = start_key
            page = self.client.query(**args)
            items.extend(_deserialize(encoded) for encoded in page.get("Items", []))
            start_key = page.get("LastEvaluatedKey")
            if not start_key:
                return items

    def _ledger(self, transition: AlarmTransition) -> Tuple[List[AlarmTransition], Dict[str, Dict[str, str]]]:
        records = self._query_prefix(alarm_key(transition), "EVENT#")
        transitions = []
        raw_by_key = {}
        for record in records:
            raw = json.loads(record["rawEvent"])
            parsed = parse_eventbridge_alarm(
                raw,
                {transition.alarm_arn},
                record.get("service", transition.service),
                record.get("environment", transition.environment),
            )
            transitions.append(parsed)
            raw_by_key[parsed.dedupe_key] = record
        return transitions, raw_by_key

    def _projection_index(self, key: str) -> Dict[str, Dict[str, str]]:
        return {item["incidentId"]: item for item in self._query_prefix(key, "PROJ#")}

    def apply(self, transition: AlarmTransition, raw_event: Dict[str, Any]) -> ApplyResult:
        key = alarm_key(transition)
        for _ in range(8):
            cursor = self._get_item(key, "STATE")
            existing, raw_by_key = self._ledger(transition)
            if transition.dedupe_key in raw_by_key:
                return ApplyResult("duplicate")
            old_index = self._projection_index(key)
            all_transitions = existing + [transition]
            desired = _desired(all_transitions, key)
            aliases = _alias_targets(old_index, desired)
            result = _result_for(transition, old_index, desired)

            version = int(cursor["version"]) + 1 if cursor else 1
            state = _cursor_record(key, version, all_transitions)
            encoded_cursor = _serialize({name: value for name, value in state.items() if name != "version"})
            encoded_cursor["version"] = {"N": state["version"]}
            put_cursor: Dict[str, Any] = {
                "TableName": self.table_name,
                "Item": encoded_cursor,
                "ConditionExpression": "#version = :old_version" if cursor else "attribute_not_exists(PK)",
            }
            if cursor:
                put_cursor["ExpressionAttributeNames"] = {"#version": "version"}
                put_cursor["ExpressionAttributeValues"] = {":old_version": {"N": cursor["version"]}}
            writes: List[Dict[str, Any]] = [{"Put": put_cursor}]
            writes.append({"Put": {
                "TableName": self.table_name,
                "Item": _serialize(_event_record(transition, raw_event)),
                "ConditionExpression": "attribute_not_exists(PK)",
            }})
            for incident_id, projected in desired.items():
                if old_index.get(incident_id, {}).get("projectionHash") == projected["index"]["projectionHash"]:
                    continue
                meta = {**projected["meta"], "PK": "INCIDENT#" + incident_id, "SK": "META"}
                writes.append({"Put": {"TableName": self.table_name, "Item": _serialize(meta)}})
                writes.append({"Put": {"TableName": self.table_name, "Item": _serialize(projected["index"])}})
            for old_id, new_id in aliases.items():
                alias = {"PK": "INCIDENT#" + old_id, "SK": "META", "aliasTo": new_id}
                writes.append({"Put": {"TableName": self.table_name, "Item": _serialize(alias)}})
                writes.append({"Delete": {
                    "TableName": self.table_name,
                    "Key": {"PK": _av_string(key), "SK": _av_string("PROJ#" + old_id)},
                }})
            _check_transaction(writes)
            try:
                self.client.transact_write_items(TransactItems=writes)
                return result
            except Exception as exc:
                code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                if code not in ("TransactionCanceledException", "ConditionalCheckFailedException"):
                    raise
        raise RuntimeError("Could not publish Incident projection after concurrent updates")

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        # Recheck the alarm cursor around the read to avoid mixing a new ledger
        # with an older projection when an ingester commits concurrently.
        for _ in range(5):
            seen = set()
            resolved = incident_id
            item = self._get_item("INCIDENT#" + resolved, "META")
            while item is not None and "aliasTo" in item:
                if resolved in seen:
                    raise RuntimeError("Incident alias cycle")
                seen.add(resolved)
                resolved = item["aliasTo"]
                item = self._get_item("INCIDENT#" + resolved, "META")
            if item is None:
                return None
            key = alarm_key_from_parts(item["account"], item["region"], item["alarmArn"])
            before = self._get_item(key, "STATE")
            current = self._get_item("INCIDENT#" + resolved, "META")
            if current is None or "aliasTo" in current:
                continue
            anchor = AlarmTransition(
                event_id=current["originalEventRef"],
                account=current["account"],
                region=current["region"],
                alarm_arn=current["alarmArn"],
                alarm_name=current["alarmName"],
                state="ALARM",
                transitioned_at=current["detectedAt"],
                service=current["service"],
                environment=current["environment"],
                dedupe_key="",
            )
            transitions, raw_by_key = self._ledger(anchor)
            desired = _desired(transitions, key)
            after = self._get_item(key, "STATE")
            if before is None or after is None or before["version"] != after["version"]:
                continue
            projected = desired.get(resolved)
            if projected is None:
                continue
            result = _public_meta(current)
            result["stateHistory"] = [
                _public_event(raw_by_key[event.dedupe_key])
                for event in projected["episode"].events
            ]
            return result
        raise RuntimeError("Could not read a stable Incident projection")

    def list_incidents(self, limit: int = 50, next_token: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        args: Dict[str, Any] = {
            "TableName": self.table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": "GSI1PK = :pk",
            "ExpressionAttributeValues": {":pk": _av_string("INCIDENTS")},
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if next_token:
            try:
                if len(next_token) > 4096:
                    raise ValueError("Token too long")
                decoded = base64.b64decode(next_token.encode("ascii"), altchars=b"-_", validate=True)
                start_key = json.loads(decoded)
                required = {"PK", "SK", "GSI1PK", "GSI1SK"}
                if not isinstance(start_key, dict) or set(start_key) != required:
                    raise ValueError("Invalid key fields")
                if any(
                    not isinstance(start_key[name], dict)
                    or not isinstance(start_key[name].get("S"), str)
                    or not start_key[name]["S"]
                    for name in required
                ) or start_key["GSI1PK"]["S"] != "INCIDENTS":
                    raise ValueError("Invalid key values")
                args["ExclusiveStartKey"] = start_key
            except (ValueError, UnicodeError, binascii.Error) as exc:
                raise ValueError("Invalid nextToken") from exc
        page = self.client.query(**args)
        items = [_public_meta(_deserialize(encoded)) for encoded in page.get("Items", [])]
        last_key = page.get("LastEvaluatedKey")
        token = base64.urlsafe_b64encode(json.dumps(last_key).encode("utf-8")).decode("ascii") if last_key else None
        return items, token
