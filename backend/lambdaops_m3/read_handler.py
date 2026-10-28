"""Authenticated HTTP API Lambda for Incident list and detail reads."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional

from .dsql_connection import build_store

INCIDENT_ID = re.compile(r"^inc_[0-9a-f]{32}$")
LOGGER = logging.getLogger(__name__)


def _unavailable(exc: Exception) -> Dict[str, Any]:
    # Do not expose SQL, connection strings, IAM tokens, or raw evidence.
    LOGGER.error("Incident read failed errorType=%s", type(exc).__name__)
    return _response(503, {"message": "Incident store unavailable"})


def _response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body, separators=(",", ":"), ensure_ascii=False),
    }


def _jwt_context(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    jwt = authorizer.get("jwt") or {}
    claims = jwt.get("claims")
    if not isinstance(claims, dict):
        return None
    # Some identity providers issue machine tokens with client_id instead of sub.
    if not any(isinstance(claims.get(name), str) and claims[name] for name in ("sub", "client_id")):
        return None
    return jwt


def handle_request(event: Dict[str, Any], store: Any, required_scope: str = "incidents:read") -> Dict[str, Any]:
    """Trust only API Gateway's validated JWT authorizer context, never token text."""

    jwt = _jwt_context(event)
    if jwt is None:
        return _response(401, {"message": "Unauthorized"})
    claims = jwt["claims"]
    granted = set()
    for scopes in (claims.get("scope"), claims.get("scp"), jwt.get("scopes")):
        if isinstance(scopes, str):
            granted.update(scopes.split())
        elif isinstance(scopes, list):
            granted.update(scope for scope in scopes if isinstance(scope, str))
    if required_scope not in granted:
        return _response(403, {"message": "Forbidden"})

    route = event.get("routeKey")
    if route == "GET /incidents/{incidentId}":
        incident_id = (event.get("pathParameters") or {}).get("incidentId")
        if not isinstance(incident_id, str) or not INCIDENT_ID.fullmatch(incident_id):
            return _response(400, {"message": "Invalid incidentId"})
        try:
            incident = store.get_incident(incident_id)
        except Exception as exc:
            return _unavailable(exc)
        if incident is None:
            return _response(404, {"message": "Incident not found"})
        return _response(200, incident)

    if route == "GET /incidents":
        params = event.get("queryStringParameters") or {}
        if getattr(store, "supports_page_queries", False):
            from .query_contract import QueryContractError, parse_incident_query
            try:
                query = parse_incident_query(params)
            except QueryContractError as exc:
                return _response(400, {"message": str(exc)})
            try:
                return _response(200, store.list_incidents_page(query))
            except Exception as exc:
                return _unavailable(exc)
        try:
            limit = int(params.get("limit", "50"))
        except (TypeError, ValueError):
            return _response(400, {"message": "Invalid limit"})
        if not 1 <= limit <= 100:
            return _response(400, {"message": "Invalid limit"})
        try:
            incidents, next_token = store.list_incidents(limit, params.get("nextToken"))
        except ValueError:
            return _response(400, {"message": "Invalid nextToken"})
        except Exception as exc:
            return _unavailable(exc)
        return _response(200, {"items": incidents, "nextToken": next_token})
    return _response(404, {"message": "Not found"})


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    return handle_request(
        event,
        build_store(read_only=True),
        os.environ.get("REQUIRED_READ_SCOPE", "incidents:read"),
    )
