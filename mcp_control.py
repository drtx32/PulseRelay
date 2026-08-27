"""PulseRelay remote MCP control plane.

MCP configures/inspects PulseRelay. It is not a data-plane outbound transport.
Authentication accepts scoped PulseRelay API keys, scoped OAuth access tokens,
or the admin bootstrap token.
"""
from __future__ import annotations

import json
import os
from typing import Any

from mcp.server import MCPServer

from core.control_plane import ControlPlaneError, ControlPlaneService
from core.mcp_auth import current_principal, get_auth_store, require_scope
from core.persistence import SQLitePhase9Store

store = SQLitePhase9Store(os.getenv("PULSERELAY_DB_PATH", "data/pulserelay.db"))
control = ControlPlaneService(store)
auth_store = get_auth_store()
mcp = MCPServer(
    "PulseRelay Control Plane",
    instructions=(
        "Configure and inspect PulseRelay. MCP is control plane only. "
        "Outbound event delivery must use webhook_outbound."
    ),
)


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc)}


def _guard(scope: str):
    try:
        require_scope(scope)
        return None
    except PermissionError as exc:
        return _error(exc)


@mcp.tool()
def list_connectors() -> dict[str, Any]:
    if denied := _guard("connectors:read"): return denied
    return {"ok": True, "connectors": control.list_connectors()}


@mcp.tool()
def get_connector(connector_id: str) -> dict[str, Any]:
    if denied := _guard("connectors:read"): return denied
    try: return {"ok": True, "connector": control.get_connector(connector_id)}
    except ControlPlaneError as exc: return _error(exc)


@mcp.tool()
def create_webhook_inbound(connector_id: str, name: str, source_type: str = "webhook",
                           max_body_bytes: int = 1048576, allowed_content_types: list[str] | None = None,
                           normalization_template: str = "", response_template: str = "",
                           enabled: bool = True) -> dict[str, Any]:
    if denied := _guard("connectors:write"): return denied
    try:
        return {"ok": True, "connector": control.create_webhook_inbound(
            connector_id, name, source_type=source_type, max_body_bytes=max_body_bytes,
            allowed_content_types=allowed_content_types, normalization_template=normalization_template,
            response_template=response_template, enabled=enabled)}
    except ControlPlaneError as exc: return _error(exc)


@mcp.tool()
def rotate_webhook_inbound_secret(connector_id: str) -> dict[str, Any]:
    if denied := _guard("connectors:write"): return denied
    try: return {"ok": True, "connector": control.rotate_webhook_inbound_secret(connector_id)}
    except ControlPlaneError as exc: return _error(exc)


@mcp.tool()
def update_webhook_inbound(connector_id: str, name: str | None = None, source_type: str | None = None,
                           enabled: bool | None = None, max_body_bytes: int | None = None,
                           allowed_content_types: list[str] | None = None,
                           normalization_template: str | None = None,
                           response_template: str | None = None) -> dict[str, Any]:
    if denied := _guard("connectors:write"): return denied
    changes = {k: v for k, v in {
        "name": name, "source_type": source_type, "enabled": enabled,
        "max_body_bytes": max_body_bytes, "allowed_content_types": allowed_content_types,
        "normalization_template": normalization_template, "response_template": response_template,
    }.items() if v is not None}
    try: return {"ok": True, "connector": control.update_webhook_inbound(connector_id, **changes)}
    except ControlPlaneError as exc: return _error(exc)


@mcp.tool()
def create_webhook_outbound(connector_id: str, name: str, url: str, method: str = "POST",
                            config: dict[str, Any] | None = None, enabled: bool = True) -> dict[str, Any]:
    if denied := _guard("connectors:write"): return denied
    try: return {"ok": True, "connector": control.create_webhook_outbound(
        connector_id, name, url, method=method, config=config, enabled=enabled)}
    except ControlPlaneError as exc: return _error(exc)


@mcp.tool()
def update_webhook_outbound(connector_id: str, name: str | None = None, url: str | None = None,
                            method: str | None = None, config: dict[str, Any] | None = None,
                            enabled: bool | None = None) -> dict[str, Any]:
    if denied := _guard("connectors:write"): return denied
    try: return {"ok": True, "connector": control.update_webhook_outbound(
        connector_id, name=name, url=url, method=method, config=config, enabled=enabled)}
    except ControlPlaneError as exc: return _error(exc)


@mcp.tool()
def set_connector_enabled(connector_id: str, enabled: bool) -> dict[str, Any]:
    if denied := _guard("connectors:write"): return denied
    try: return {"ok": True, "connector": control.set_connector_enabled(connector_id, enabled)}
    except ControlPlaneError as exc: return _error(exc)


@mcp.tool()
def list_routes() -> dict[str, Any]:
    if denied := _guard("routes:read"): return denied
    return {"ok": True, "routes": control.list_routes()}


@mcp.tool()
def get_route(route_id: str) -> dict[str, Any]:
    if denied := _guard("routes:read"): return denied
    try: return {"ok": True, "route": control.get_route(route_id)}
    except ControlPlaneError as exc: return _error(exc)


@mcp.tool()
def validate_route(match: dict[str, Any], destinations: list[str]) -> dict[str, Any]:
    if denied := _guard("routes:read"): return denied
    return {"ok": True, "validation": control.validate_route(match, destinations)}


@mcp.tool()
def create_or_update_route(route_id: str, name: str, match: dict[str, Any], destinations: list[str],
                           aggregation: dict[str, Any] | None = None, trigger: dict[str, Any] | None = None,
                           enabled: bool = True) -> dict[str, Any]:
    if denied := _guard("routes:write"): return denied
    try: return {"ok": True, "route": control.create_or_update_route(
        route_id, name, match=match, destinations=destinations,
        aggregation=aggregation, trigger=trigger, enabled=enabled)}
    except ControlPlaneError as exc: return _error(exc)


@mcp.tool()
def set_route_enabled(route_id: str, enabled: bool) -> dict[str, Any]:
    if denied := _guard("routes:write"): return denied
    try: return {"ok": True, "route": control.set_route_enabled(route_id, enabled)}
    except ControlPlaneError as exc: return _error(exc)


@mcp.tool()
def get_topology() -> dict[str, Any]:
    if denied := _guard("routes:read"): return denied
    return {"ok": True, "topology": control.topology()}


@mcp.tool()
def list_deliveries(limit: int = 50, status: str | None = None) -> dict[str, Any]:
    if denied := _guard("deliveries:read"): return denied
    return {"ok": True, "deliveries": control.list_deliveries(limit=limit, status=status)}


class MCPControlPlaneAuth:
    """ASGI bearer gate resolving API keys and OAuth tokens to scoped principals."""
    def __init__(self, wrapped): self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.wrapped(scope, receive, send); return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, token = supplied.partition(" ")
        principal = auth_store.authenticate_bearer(token.strip()) if scheme.lower() == "bearer" and token else None
        if principal is None:
            await self._reject(send); return
        reset = current_principal.set(principal)
        try: await self.wrapped(scope, receive, send)
        finally: current_principal.reset(reset)

    @staticmethod
    async def _reject(send):
        body = json.dumps({"error": "authentication required"}).encode()
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b"Bearer") ]})
        await send({"type": "http.response.body", "body": body})


mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", json_response=True,
                                  host=os.getenv("PULSERELAY_MCP_HOST", "127.0.0.1"))
app = MCPControlPlaneAuth(mcp_app)
