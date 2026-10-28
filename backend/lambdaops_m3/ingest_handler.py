"""SQS-triggered Lambda entry point for CloudWatch alarm state changes."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Iterable

from .contracts import parse_eventbridge_alarm
from .dsql_connection import build_store

LOGGER = logging.getLogger(__name__)


def process_batch(
    event: Dict[str, Any],
    store: Any,
    allowed_alarm_arns: Iterable[str],
    service: str,
    environment: str,
) -> Dict[str, Any]:
    """Process each SQS record separately so Lambda retries only failed messages."""

    records = event.get("Records")
    if not isinstance(records, list):
        raise ValueError("Expected SQS Records list")
    failures = []
    allowed = set(allowed_alarm_arns)
    for record in records:
        message_id = record.get("messageId") if isinstance(record, dict) else None
        if not isinstance(message_id, str) or not message_id:
            # A malformed Lambda envelope cannot be acknowledged safely.
            raise ValueError("SQS record is missing messageId")
        try:
            if record.get("eventSource") != "aws:sqs":
                raise ValueError("Expected SQS eventSource")
            body = record.get("body")
            if not isinstance(body, str):
                raise ValueError("SQS record body must be JSON text")
            raw_event = json.loads(body)
            transition = parse_eventbridge_alarm(raw_event, allowed, service, environment)
            result = store.apply(transition, raw_event)
            LOGGER.info(
                "Processed alarm transition messageId=%s state=%s outcome=%s incidentId=%s",
                message_id,
                transition.state,
                result.outcome,
                result.incident_id,
            )
        except Exception:
            LOGGER.exception("Alarm transition processing failed for SQS messageId=%s", message_id)
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    allowed = json.loads(os.environ["ALARM_ARNS"])
    if not isinstance(allowed, list) or not allowed:
        raise ValueError("ALARM_ARNS must be a non-empty JSON array")
    return process_batch(
        event,
        build_store(),
        allowed,
        os.environ["SERVICE"],
        os.environ["ENVIRONMENT"],
    )
