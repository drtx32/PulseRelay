"""Shared PulseRelay control-plane services.

This module is deliberately transport-agnostic. HTTP, Web UI, CLI, and MCP
must call the same service methods instead of re-implementing connector/route
business logic in their protocol handlers.

MCP is a control plane only. Proactive outbound delivery remains webhook-only.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import asdict
from typing import Any

from core.persistence import SQLitePhase9Store
from core.redaction import (
    redact_mapping,
    redact_sensitive_text,
    redact_url,
    restore_redacted_url,
    restore_redacted_values,
)


def hash_secret(secret: str) -> str:
    """Hash a generated inbound credential using the API-compatible format."""
    salt = secrets.token_bytes(16)
    iterations = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_secret(secret: str, secret_hash: str) -> bool:
    """Verify both current PBKDF2 hashes and legacy SHA256 hashes."""
    try:
        algorithm, iterations, salt_hex, digest_hex = secret_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            raise ValueError
        rounds = int(iterations)
        if not 100_000 <= rounds <= 1_000_000:
            raise ValueError
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, rounds)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return hmac.compare_digest(hashlib.sha256(secret.encode("utf-8")).hexdigest(), secret_hash)


class ControlPlaneError(ValueError):
    pass


class ControlPlaneService:
    """Canonical configuration/inspection facade for PulseRelay.

    The service intentionally exposes webhook inbound/outbound and routing
    configuration. It does *not* expose an MCP outbound destination type.
    """

    def __init__(self, store: SQLitePhase9Store):
        self.store = store

    def list_flows(self): return [self._public_flow(x) for x in self.store.list_flows()]
    def get_flow(self, flow_id):
        item=self.store.get_flow(flow_id)
        if not item: raise ControlPlaneError("flow not found")
        return self._public_flow(item)
    @staticmethod
    def _public_flow(item):
        return redact_mapping(item)
    def validate_flow(self, flow):
        from core.flows import validate_flow
        result=validate_flow(flow)
        for node in flow.get('nodes',[]):
            endpoint=node.get('endpoint_id')
            if node.get('type')=='endpoint_trigger' and (not endpoint or not self.store.get_inbound_endpoint(endpoint) or not self.store.get_inbound_endpoint(endpoint).enabled): result['errors'].append('endpoint trigger connector is missing or disabled'); result['valid']=False
            if node.get('type')=='webhook_outbound' and (not endpoint or not self.store.get_destination(endpoint) or not self.store.get_destination(endpoint).enabled): result['errors'].append('webhook outbound connector is missing or disabled'); result['valid']=False
        return result
    def create_flow(self, flow):
        result=self.validate_flow(flow)
        if not result['valid']: raise ControlPlaneError('; '.join(result['errors']))
        self.store.upsert_flow(flow['id'],flow['name'],flow.get('enabled',True),flow['nodes'],flow.get('edges',[]));return self.get_flow(flow['id'])
    def update_flow(self, flow_id, changes):
        current=self.store.get_flow(flow_id)
        if not current: raise ControlPlaneError('flow not found')
        return self.create_flow(current|changes|{'id':flow_id})
    def disable_flow(self, flow_id):
        if not self.store.set_flow_enabled(flow_id,False): raise ControlPlaneError('flow not found')
        return self.get_flow(flow_id)
    def get_flow_run(self, run_id):
        run=self.store.get_flow_run(run_id)
        if not run: raise ControlPlaneError('flow run not found')
        for node_run in run.get('node_runs', []):
            node_run.pop('input_json', None)
            node_run.pop('output_json', None)
        return redact_mapping(run)
    def list_flow_runs(self, limit=100): return [self.get_flow_run(run['id']) for run in self.store.list_flow_runs(limit)]

    @staticmethod
    def _public_inbound(record) -> dict[str, Any]:
        return {
            "id": record.id,
            "name": record.name,
            "kind": "webhook_inbound",
            "enabled": record.enabled,
            "source_type": record.source_type,
            "max_body_bytes": record.max_body_bytes,
            "allowed_content_types": list(record.allowed_content_types),
            "hmac_enabled": bool(record.hmac_secret_ref),
        }

    @staticmethod
    def _public_outbound(record) -> dict[str, Any]:
        # Destination config may contain secret references but must never carry
        # materialized Authorization values through the control plane.
        config = redact_mapping(dict(record.config or {}))
        return {
            "id": record.id,
            "name": record.name,
            "kind": "webhook_outbound",
            "enabled": record.enabled,
            "url": redact_url(record.url),
            "method": record.method,
            "config": config,
        }

    def list_connectors(self) -> list[dict[str, Any]]:
        inbound = [self._public_inbound(item) for item in self.store.list_inbound_endpoints()]
        outbound = [self._public_outbound(item) for item in self.store.list_destinations()]
        return inbound + outbound

    def get_connector(self, connector_id: str) -> dict[str, Any]:
        inbound = self.store.get_inbound_endpoint(connector_id)
        if inbound is not None:
            return self._public_inbound(inbound)
        outbound = self.store.get_destination(connector_id)
        if outbound is not None:
            return self._public_outbound(outbound)
        raise ControlPlaneError(f"connector not found: {connector_id}")

    def create_webhook_inbound(
        self,
        connector_id: str,
        name: str,
        *,
        source_type: str = "webhook",
        max_body_bytes: int = 1_048_576,
        allowed_content_types: list[str] | None = None,
        normalization_template: str = "",
        response_template: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        if self.store.get_inbound_endpoint(connector_id) is not None:
            raise ControlPlaneError(f"connector already exists: {connector_id}")
        if self.store.get_destination(connector_id) is not None:
            raise ControlPlaneError(f"connector id already used by outbound endpoint: {connector_id}")
        if max_body_bytes <= 0 or max_body_bytes > 16 * 1024 * 1024:
            raise ControlPlaneError("max_body_bytes must be between 1 and 16777216")
        secret = secrets.token_urlsafe(32)
        self.store.create_inbound_endpoint(
            connector_id,
            name,
            hash_secret(secret),
            source_type=source_type,
            enabled=enabled,
            response_template=response_template,
            normalization_template=normalization_template,
            max_body_bytes=max_body_bytes,
            allowed_content_types=allowed_content_types or ["application/json"],
        )
        result = self.get_connector(connector_id)
        # Creation/rotation is the only time plaintext is returned.
        result["secret"] = secret
        result["path"] = f"/v1/hooks/{connector_id}/{secret}"
        return result

    def rotate_webhook_inbound_secret(self, connector_id: str) -> dict[str, Any]:
        if self.store.get_inbound_endpoint(connector_id) is None:
            raise ControlPlaneError(f"inbound connector not found: {connector_id}")
        secret = secrets.token_urlsafe(32)
        self.store.update_inbound_endpoint(connector_id, secret_hash=hash_secret(secret))
        result = self.get_connector(connector_id)
        result["secret"] = secret
        result["path"] = f"/v1/hooks/{connector_id}/{secret}"
        return result

    def update_webhook_inbound(self, connector_id: str, **changes: Any) -> dict[str, Any]:
        if self.store.get_inbound_endpoint(connector_id) is None:
            raise ControlPlaneError(f"inbound connector not found: {connector_id}")
        allowed = {
            "name", "enabled", "source_type", "response_template", "normalization_template",
            "max_body_bytes", "allowed_content_types", "hmac_secret_ref", "hmac_header",
            "hmac_timestamp_header", "hmac_max_age_seconds",
        }
        filtered = {key: value for key, value in changes.items() if key in allowed}
        self.store.update_inbound_endpoint(connector_id, **filtered)
        return self.get_connector(connector_id)

    def create_webhook_outbound(
        self,
        connector_id: str,
        name: str,
        url: str,
        *,
        method: str = "POST",
        config: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        if self.store.get_destination(connector_id) is not None:
            raise ControlPlaneError(f"connector already exists: {connector_id}")
        if self.store.get_inbound_endpoint(connector_id) is not None:
            raise ControlPlaneError(f"connector id already used by inbound endpoint: {connector_id}")
        if not url.startswith(("http://", "https://")):
            raise ControlPlaneError("webhook outbound url must use http or https")
        self.store.upsert_destination(connector_id, name, url, method, config or {}, enabled)
        return self.get_connector(connector_id)

    def update_webhook_outbound(
        self,
        connector_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        method: str | None = None,
        config: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        current = self.store.get_destination(connector_id)
        if current is None:
            raise ControlPlaneError(f"outbound connector not found: {connector_id}")
        next_url = current.url if url is None else restore_redacted_url(current.url, url)
        if not next_url.startswith(("http://", "https://")):
            raise ControlPlaneError("webhook outbound url must use http or https")
        next_config = current.config if config is None else restore_redacted_values(current.config, config)
        self.store.upsert_destination(
            connector_id,
            current.name if name is None else name,
            next_url,
            current.method if method is None else method,
            next_config,
            current.enabled if enabled is None else enabled,
        )
        return self.get_connector(connector_id)

    def set_connector_enabled(self, connector_id: str, enabled: bool) -> dict[str, Any]:
        inbound = self.store.get_inbound_endpoint(connector_id)
        if inbound is not None:
            self.store.update_inbound_endpoint(connector_id, enabled=enabled)
            return self.get_connector(connector_id)
        outbound = self.store.get_destination(connector_id)
        if outbound is not None:
            self.store.upsert_destination(
                outbound.id, outbound.name, outbound.url, outbound.method, outbound.config, enabled
            )
            return self.get_connector(connector_id)
        raise ControlPlaneError(f"connector not found: {connector_id}")

    def list_routes(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for route in self.store.list_routes():
            result.append({
                "id": route.id,
                "name": route.name,
                "enabled": route.enabled,
                "match": route.match,
                "aggregation": route.aggregation,
                "trigger": route.trigger,
                "destinations": self.store.route_destinations(route.id),
            })
        return result

    def get_route(self, route_id: str) -> dict[str, Any]:
        for route in self.list_routes():
            if route["id"] == route_id:
                return route
        raise ControlPlaneError(f"route not found: {route_id}")

    def validate_route(
        self,
        match: dict[str, Any],
        destinations: list[str],
    ) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        if not isinstance(match, dict) or not match:
            warnings.append("route has no match constraints and may match every event")
        if not destinations:
            errors.append("route must have at least one webhook outbound destination")
        for destination_id in destinations:
            destination = self.store.get_destination(destination_id)
            if destination is None:
                errors.append(f"outbound destination not found: {destination_id}")
            elif not destination.enabled:
                warnings.append(f"outbound destination is disabled: {destination_id}")
        return {"valid": not errors, "errors": errors, "warnings": warnings}

    def create_or_update_route(
        self,
        route_id: str,
        name: str,
        *,
        match: dict[str, Any] | None = None,
        destinations: list[str] | None = None,
        aggregation: dict[str, Any] | None = None,
        trigger: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        destination_ids = list(destinations or [])
        validation = self.validate_route(match or {}, destination_ids)
        if not validation["valid"]:
            raise ControlPlaneError("; ".join(validation["errors"]))
        self.store.upsert_route(
            route_id,
            name,
            match or {},
            aggregation or {},
            trigger or {"mode": "immediate"},
            enabled,
            destination_ids,
        )
        result = self.get_route(route_id)
        result["validation"] = validation
        return result

    def set_route_enabled(self, route_id: str, enabled: bool) -> dict[str, Any]:
        current = self.get_route(route_id)
        self.store.upsert_route(
            route_id,
            current["name"],
            current["match"],
            current["aggregation"],
            current["trigger"],
            enabled,
            current["destinations"],
        )
        return self.get_route(route_id)

    def topology(self) -> dict[str, Any]:
        connectors = self.list_connectors()
        routes = self.list_routes()
        edges: list[dict[str, str]] = []
        for route in routes:
            source = str(route["match"].get("source.type") or route["match"].get("source.id") or "event_bus")
            for destination in route["destinations"]:
                edges.append({"from": source, "via": route["id"], "to": destination})
        return {"connectors": connectors, "routes": routes, "edges": edges}

    def list_deliveries(self, *, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        return [self._public_delivery(item) for item in self.store.list_deliveries(limit=limit, status=status)]

    @staticmethod
    def _public_delivery(item) -> dict[str, Any]:
        result = asdict(item)
        for key in ("request_excerpt", "response_excerpt", "last_error"):
            result[key] = redact_sensitive_text(str(result.get(key) or ""))
        return result

    def get_delivery(self, delivery_id: str) -> dict[str, Any]:
        item = self.store.get_delivery(delivery_id)
        if item is None:
            raise ControlPlaneError(f"delivery not found: {delivery_id}")
        return self._public_delivery(item)

    def list_events(self, *, limit: int = 50, source_type: str | None = None,
                    event_type: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        return [redact_mapping(asdict(item)) for item in self.store.list_events(
            limit=limit, source_type=source_type, event_type=event_type, status=status)]

    def get_event(self, event_id: str) -> dict[str, Any]:
        payload = self.store.get_event_payload(event_id)
        if not payload:
            raise ControlPlaneError(f"event not found: {event_id}")
        return redact_mapping(payload)
