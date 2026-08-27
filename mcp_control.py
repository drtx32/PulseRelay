"""Remote MCP control plane for PulseRelay.

Run separately from the data-plane API when desired. Both processes share the
same SQLite store and ControlPlaneService. MCP changes configuration; events
still enter through sources/inbound webhooks and leave through Webhook
Outbound.
"""
from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer

from core.control_plane import ControlPlaneError, ControlPlaneService
from core.persistence import SQLitePhase9Store


store = SQLitePhase9Store(os.getenv("PULSERELAY_DB_PATH", "data/pulserelay.db"))
control = ControlPlaneService(store)
mcp = MCPServer(
    "PulseRelay Control Plane",
    instructions=(
        "Configure and inspect PulseRelay. MCP is the control plane only. "
        "Proactive outbound message delivery must use webhook_outbound; "
        "never model MCP as a data-plane outbound destination."
    ),
)


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc)}


@mcp.tool()
def list_connectors() -> dict[str, Any]:
    """List PulseRelay webhook inbound/outbound endpoints."""
    return {"ok": True, "connectors": control.list_connectors()}


@mcp.tool()
def get_connector(connector_id: str) -> dict[str, Any]:
    """Read one connector without exposing stored plaintext credentials."""
    try:
        return {"ok": True, "connector": control.get_connector(connector_id)}
    except ControlPlaneError as exc:
        return _error(exc)


@mcp.tool()
def create_webhook_inbound(
    connector_id: str,
    name: str,
    source_type: str = "webhook",
    max_body_bytes: int = 1048576,
    allowed_content_types: list[str] | None = None,
    normalization_template: str = "",
    response_template: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    """Create an authenticated inbound webhook endpoint.

    The plaintext secret/path is returned only by this creation operation (or
    an explicit rotation); normal reads never expose it.
    """
    try:
        connector = control.create_webhook_inbound(
            connector_id,
            name,
            source_type=source_type,
            max_body_bytes=max_body_bytes,
            allowed_content_types=allowed_content_types,
            normalization_template=normalization_template,
            response_template=response_template,
            enabled=enabled,
        )
        return {"ok": True, "connector": connector}
    except ControlPlaneError as exc:
        return _error(exc)


@mcp.tool()
def rotate_webhook_inbound_secret(connector_id: str) -> dict[str, Any]:
    """Rotate an inbound webhook secret and return the new path once."""
    try:
        return {"ok": True, "connector": control.rotate_webhook_inbound_secret(connector_id)}
    except ControlPlaneError as exc:
        return _error(exc)


@mcp.tool()
def update_webhook_inbound(
    connector_id: str,
    name: str | None = None,
    source_type: str | None = None,
    enabled: bool | None = None,
    max_body_bytes: int | None = None,
    allowed_content_types: list[str] | None = None,
    normalization_template: str | None = None,
    response_template: str | None = None,
) -> dict[str, Any]:
    """Update safe inbound-webhook configuration fields."""
    changes = {
        key: value
        for key, value in {
            "name": name,
            "source_type": source_type,
            "enabled": enabled,
            "max_body_bytes": max_body_bytes,
            "allowed_content_types": allowed_content_types,
            "normalization_template": normalization_template,
            "response_template": response_template,
        }.items()
        if value is not None
    }
    try:
        return {"ok": True, "connector": control.update_webhook_inbound(connector_id, **changes)}
    except ControlPlaneError as exc:
        return _error(exc)


@mcp.tool()
def create_webhook_outbound(
    connector_id: str,
    name: str,
    url: str,
    method: str = "POST",
    config: dict[str, Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Create the proactive outbound endpoint used by the data plane."""
    try:
        connector = control.create_webhook_outbound(
            connector_id, name, url, method=method, config=config, enabled=enabled
        )
        return {"ok": True, "connector": connector}
    except ControlPlaneError as exc:
        return _error(exc)


@mcp.tool()
def update_webhook_outbound(
    connector_id: str,
    name: str | None = None,
    url: str | None = None,
    method: str | None = None,
    config: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Update a Webhook Outbound endpoint; there is intentionally no MCP outbound type."""
    try:
        connector = control.update_webhook_outbound(
            connector_id,
            name=name,
            url=url,
            method=method,
            config=config,
            enabled=enabled,
        )
        return {"ok": True, "connector": connector}
    except ControlPlaneError as exc:
        return _error(exc)


@mcp.tool()
def set_connector_enabled(connector_id: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable an endpoint. Disabled is a hard configuration gate."""
    try:
        return {"ok": True, "connector": control.set_connector_enabled(connector_id, enabled)}
    except ControlPlaneError as exc:
        return _error(exc)


@mcp.tool()
def list_routes() -> dict[str, Any]:
    """List routing configuration and its webhook outbound bindings."""
    return {"ok": True, "routes": control.list_routes()}


@mcp.tool()
def get_route(route_id: str) -> dict[str, Any]:
    """Read one route and its outbound endpoint IDs."""
    try:
        return {"ok": True, "route": control.get_route(route_id)}
    except ControlPlaneError as exc:
        return _error(exc)


@mcp.tool()
def validate_route(match: dict[str, Any], destinations: list[str]) -> dict[str, Any]:
    """Validate a prospective route before persisting/enabling it."""
    return {"ok": True, "validation": control.validate_route(match, destinations)}


@mcp.tool()
def create_or_update_route(
    route_id: str,
    name: str,
    match: dict[str, Any],
    destinations: list[str],
    aggregation: dict[str, Any] | None = None,
    trigger: dict[str, Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Create/update a route from event/source matching to Webhook Outbound destinations."""
    try:
        route = control.create_or_update_route(
            route_id,
            name,
            match=match,
            destinations=destinations,
            aggregation=aggregation,
            trigger=trigger,
            enabled=enabled,
        )
        return {"ok": True, "route": route}
    except ControlPlaneError as exc:
        return _error(exc)


@mcp.tool()
def set_route_enabled(route_id: str, enabled: bool) -> dict[str, Any]:
    """Enable/disable a route without destroying its configuration."""
    try:
        return {"ok": True, "route": control.set_route_enabled(route_id, enabled)}
    except ControlPlaneError as exc:
        return _error(exc)


@mcp.tool()
def get_topology() -> dict[str, Any]:
    """Return the current connector/route graph for remote configuration agents."""
    return {"ok": True, "topology": control.topology()}


@mcp.tool()
def list_deliveries(limit: int = 50, status: str | None = None) -> dict[str, Any]:
    """Inspect recent webhook delivery state. This does not pull message payloads as delivery."""
    return {"ok": True, "deliveries": control.list_deliveries(limit=limit, status=status)}


# The returned ASGI application includes /mcp and owns its session-manager
# lifespan when it is served directly. Running it as a separate service avoids
# coupling MCP transport lifecycle to the data-plane FastAPI process.
app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    host=os.getenv("PULSERELAY_MCP_HOST", "127.0.0.1"),
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("PULSERELAY_MCP_BIND", "127.0.0.1"),
        port=int(os.getenv("PULSERELAY_MCP_PORT", "8010")),
    )
