"""Webhook-native HTTP surface for the durable PulseRelay service."""
from __future__ import annotations

import hashlib
import hmac
import asyncio
import json
import math
import os
import re
import secrets
import time
from typing import Any
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, build_opener

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2.sandbox import SandboxedEnvironment
from jinja2 import StrictUndefined, nodes
import yaml

from core.event import ensure_event
from core.persistence import SQLitePhase9Store
from core.relay import DurableRelay
from core.connector_index import get_connector_index
from core.webhook_delivery import WebhookDelivery
from core.event_bus import EventBus


def _hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def _valid_secret(secret: str, secret_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = secret_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256": raise ValueError
        iterations = int(iterations)
        if not 100_000 <= iterations <= 1_000_000: raise ValueError
        expected = bytes.fromhex(digest_hex)
        salt = bytes.fromhex(salt_hex)
        if len(salt) != 16 or len(expected) != hashlib.sha256().digest_size: raise ValueError
        actual = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        # Compatibility for endpoints created before salted hashes were added.
        return hmac.compare_digest(hashlib.sha256(secret.encode("utf-8")).hexdigest(), secret_hash)


def _require_read_auth(request: Request) -> None:
    """Require the PulseRelay admin bearer before exposing event data."""
    expected = os.getenv("PULSERELAY_ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(503, "event read authentication is not configured")
    supplied = request.headers.get("authorization", "")
    scheme, _, token = supplied.partition(" ")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token.strip(), expected):
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})


def _require_admin_auth(request: Request) -> None:
    _require_read_auth(request)


class EventStreamHub:
    """In-process fan-out for the browser Event Stream WebSocket."""

    def __init__(self):
        self.connections: set[WebSocket] = set()
        self.sse_subscribers: set[asyncio.Queue] = set()

    async def broadcast(self, event: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for connection in tuple(self.connections):
            try:
                await connection.send_json(event)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.connections.discard(connection)
        for subscriber in tuple(self.sse_subscribers):
            try:
                subscriber.put_nowait(event)
            except asyncio.QueueFull:
                self.sse_subscribers.discard(subscriber)


def _gbrain_admin_call(path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    """Call GBrain's cookie-gated admin API without forwarding its secret to the UI."""
    base = os.getenv("GBRAIN_ADMIN_URL", "").strip().rstrip("/")
    bootstrap = os.getenv("GBRAIN_ADMIN_BOOTSTRAP_TOKEN", "").strip()
    if not base or not bootstrap:
        raise HTTPException(503, "GBrain admin integration is not configured")
    jar = CookieJar()
    opener = build_opener(__import__("urllib.request", fromlist=["HTTPCookieProcessor"]).HTTPCookieProcessor(jar))
    try:
        login_body = json.dumps({"token": bootstrap}).encode()
        login = UrlRequest(f"{base}/admin/login", data=login_body, method="POST", headers={"Content-Type": "application/json"})
        with opener.open(login, timeout=10) as response:
            if response.status != 200:
                raise HTTPException(502, "GBrain admin login failed")
            # GBrain marks the admin cookie Secure even on an internal HTTP
            # hop. Keep the cookie server-side and attach only its value to
            # the same-origin admin API call; it never reaches the browser.
            set_cookie = response.headers.get("Set-Cookie", "")
            cookie_match = re.search(r"(?:^|;\s*)gbrain_admin=([^;]+)", set_cookie)
            if not cookie_match:
                raise HTTPException(502, "GBrain admin session was not issued")
            admin_cookie = cookie_match.group(1)
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Cookie": f"gbrain_admin={admin_cookie}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = UrlRequest(f"{base}{path}", data=body, method=method, headers=headers)
        with opener.open(request, timeout=10) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except HTTPException:
        raise
    except HTTPError as exc:
        try: detail = json.loads(exc.read() or b"{}").get("error", "GBrain admin request failed")
        except Exception: detail = "GBrain admin request failed"
        raise HTTPException(exc.code if 400 <= exc.code < 600 else 502, detail) from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise HTTPException(502, "GBrain admin request unavailable") from exc


def _render(template: str, *, event: dict[str, Any], duplicate: bool) -> str:
    if len(template.encode("utf-8")) > 32 * 1024:
        raise ValueError("template is too large")
    environment = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined, cache_size=0)
    # Keep only data-oriented rendering. In particular, do not expose range,
    # cycler, namespace, joiner, imports, or user-defined filters.
    environment.globals.clear()
    environment.filters.clear()
    environment.tests.clear()
    tree = environment.parse(template)
    forbidden = (nodes.For, nodes.Macro, nodes.Call, nodes.Import, nodes.FromImport, nodes.Include, nodes.Extends)
    if any(next(tree.find_all(kind), None) is not None for kind in forbidden):
        raise ValueError("template contains a forbidden construct")
    rendered = environment.from_string(template).render(event=event, duplicate=duplicate)
    if len(rendered.encode("utf-8")) > 256 * 1024:
        raise ValueError("rendered template is too large")
    return rendered


async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, "request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_hmac_age(value: Any) -> int:
    try: age = int(value)
    except (TypeError, ValueError) as exc: raise HTTPException(400, "invalid hmac_max_age_seconds") from exc
    if age <= 0 or age > 86_400: raise HTTPException(400, "hmac_max_age_seconds out of range")
    return age


def _endpoint_public(endpoint, base_url: str = "/v1/hooks") -> dict[str, Any]:
    return {"id": endpoint.id, "name": endpoint.name, "enabled": endpoint.enabled,
            "source_type": endpoint.source_type, "max_body_bytes": endpoint.max_body_bytes,
            "allowed_content_types": endpoint.allowed_content_types, "hmac_enabled": bool(endpoint.hmac_secret_ref)}


def _sync_connector_webhooks(store: SQLitePhase9Store, source_type: str) -> None:
    """Materialize route-bound outbound webhooks into the connector manifest.

    Routes remain the source of truth for delivery. The manifest is updated as
    the connector's runtime-facing, human-readable projection so the
    connector supervisor and the Connectors tab see the same bindings.
    """
    root = Path(os.getenv("PULSERELAY_CONNECTOR_ROOT", "connectors"))
    candidates = {source_type, source_type.replace("_", "-")}
    manifest_path: Path | None = None
    for path in sorted(root.glob("*/connector.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if path.parent.name in candidates or str(data.get("id", "")) in candidates:
            manifest_path = path
            break
    if manifest_path is None:
        return
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return
        raw = data.get("webhooks", []) or []
        if not isinstance(raw, list):
            raw = []
        existing: list[dict[str, Any]] = []
        existing_urls: set[str] = set()
        for item in raw:
            item = {"url": item} if isinstance(item, str) else item
            if not isinstance(item, dict) or not str(item.get("url", "")).strip():
                continue
            normalized = dict(item)
            normalized["url"] = str(item["url"]).strip()
            normalized.setdefault("name", normalized["url"])
            normalized.setdefault("enabled", True)
            existing.append(normalized)
            existing_urls.add(normalized["url"])
        bound: list[dict[str, Any]] = []
        bound_urls: set[str] = set()
        for route in store.list_routes():
            route_source = route.match.get("source.type") or str(route.match.get("event.type", "")).split(".", 1)[0]
            if route_source != source_type:
                continue
            for destination_id in store.route_destinations(route.id):
                destination = store.get_destination(destination_id)
                if destination is None or destination.url in bound_urls:
                    continue
                bound.append({"name": destination.name, "url": destination.url, "enabled": destination.enabled})
                bound_urls.add(destination.url)
        if data.get("webhooks", []) == bound:
            return
        data["webhooks"] = bound
        temporary = manifest_path.with_suffix(".yaml.tmp")
        temporary.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(manifest_path)
    except OSError:
        # A read-only or unavailable mount must not prevent route creation.
        return


def _connector_aggregation(source_type: str) -> dict[str, Any]:
    root = Path(os.getenv("PULSERELAY_CONNECTOR_ROOT", "connectors"))
    candidates = {source_type, source_type.replace("_", "-")}
    cached = get_connector_index(root, os.getenv("PULSERELAY_CONNECTOR_INDEX_PATH", "/data/connectors.index.json")).find(source_type)
    if cached is not None:
        value = cached.get("manifest_json", {}).get("aggregation", {})
        return dict(value) if isinstance(value, dict) else {}
    for path in sorted(root.glob("*/connector.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if path.parent.name in candidates or str(data.get("id", "")) in candidates:
            value = data.get("aggregation", {})
            return dict(value) if isinstance(value, dict) else {}
    return {}


def _route_source_type(route) -> str:
    return str(route.match.get("source.type") or str(route.match.get("event.type", "")).split(".", 1)[0])


def _sync_connector_aggregation(store: SQLitePhase9Store, source_type: str, aggregation: dict[str, Any]) -> None:
    """Keep connector-level aggregation changes in the routes that execute them."""
    for route in store.list_routes():
        if _route_source_type(route) != source_type:
            continue
        store.upsert_route(
            route.id,
            route.name,
            route.match,
            dict(aggregation),
            route.trigger,
            route.enabled,
            store.route_destinations(route.id),
        )


def create_app(store: SQLitePhase9Store | None = None) -> FastAPI:
    store = store or SQLitePhase9Store(os.getenv("PULSERELAY_DB_PATH", "data/pulserelay.db"))
    event_bus = EventBus()
    relay = DurableRelay(store, event_bus=event_bus)
    connector_root = Path(os.getenv("PULSERELAY_CONNECTOR_ROOT", "connectors"))
    connector_index = get_connector_index(connector_root, os.getenv("PULSERELAY_CONNECTOR_INDEX_PATH", "/data/connectors.index.json"))
    connector_index.refresh(force=True)
    app = FastAPI(title="PulseRelay", version="1.0")
    event_stream = EventStreamHub()
    event_bus_task: asyncio.Task | None = None

    async def forward_bus_events() -> None:
        queue = event_bus.subscribe()
        try:
            while True:
                await event_stream.broadcast(await queue.get())
        finally:
            event_bus.unsubscribe(queue)

    @app.on_event("startup")
    async def start_event_bus_bridge():
        nonlocal event_bus_task
        event_bus_task = asyncio.create_task(forward_bus_events())

    @app.on_event("shutdown")
    async def stop_event_bus_bridge():
        if event_bus_task:
            event_bus_task.cancel()
            try:
                await event_bus_task
            except asyncio.CancelledError:
                pass
    assets_dir = os.path.join(os.path.dirname(__file__), "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    async def console():
        return FileResponse(os.path.join(assets_dir, "index.html"), headers={"Cache-Control": "no-store, max-age=0"})

    @app.get("/healthz")
    async def healthz(): return {"status": "ok"}

    @app.websocket("/v1/events/stream")
    async def event_stream_socket(websocket: WebSocket):
        expected = os.getenv("PULSERELAY_ADMIN_TOKEN", "").strip()
        await websocket.accept()
        try:
            auth_message = await websocket.receive_text()
            try:
                auth_payload = json.loads(auth_message)
                supplied = str(auth_payload.get("token", "")).strip()
            except (TypeError, json.JSONDecodeError):
                supplied = auth_message.strip()
            if not expected or not supplied or not hmac.compare_digest(supplied, expected):
                await websocket.close(code=1008)
                return
            event_stream.connections.add(websocket)
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            event_stream.connections.discard(websocket)

    @app.get("/v1/events/stream")
    async def event_stream_sse(request: Request, token: str = ""):
        expected = os.getenv("PULSERELAY_ADMIN_TOKEN", "").strip()
        if not expected or not token or not hmac.compare_digest(token.strip(), expected):
            raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        event_stream.sse_subscribers.add(queue)

        async def stream():
            try:
                yield ": connected\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                event_stream.sse_subscribers.discard(queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    @app.post("/v1/events")
    async def events(request: Request):
        try: payload = await request.json()
        except Exception as exc: raise HTTPException(400, "invalid JSON") from exc
        try:
            event, duplicate = relay.ingest(payload, source_type="api")
        except Exception as exc:
            raise HTTPException(500, "event persistence failed") from exc
        return {"accepted": True, "event_id": event.id, "duplicate": duplicate}

    @app.post("/v1/hooks")
    async def create_hook(payload: dict[str, Any], request: Request):
        _require_admin_auth(request)
        endpoint_id = str(payload.get("id") or f"hook_{secrets.token_hex(8)}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", endpoint_id):
            raise HTTPException(400, "invalid endpoint id")
        secret = str(payload.get("secret") or secrets.token_urlsafe(32))
        if len(secret) < 16:
            raise HTTPException(400, "secret must contain at least 16 characters")
        if store.get_inbound_endpoint(endpoint_id):
            raise HTTPException(409, "endpoint already exists")
        try: max_body = int(payload.get("max_body_bytes", 1048576))
        except (TypeError, ValueError) as exc: raise HTTPException(400, "invalid max_body_bytes") from exc
        if max_body <= 0 or max_body > 50 * 1024 * 1024:
            raise HTTPException(400, "max_body_bytes out of range")
        allowed_content_types = payload.get("allowed_content_types") or ["application/json"]
        if not isinstance(allowed_content_types, list) or not all(isinstance(item, str) and item.strip() for item in allowed_content_types):
            raise HTTPException(400, "allowed_content_types must be a non-empty list of strings")
        hmac_max_age = _validate_hmac_age(payload.get("hmac_max_age_seconds", 300))
        store.create_inbound_endpoint(endpoint_id, str(payload.get("name") or endpoint_id), _hash_secret(secret),
            source_type=str(payload.get("source_type") or "webhook"), enabled=bool(payload.get("enabled", True)),
            response_template=str(payload.get("response_template") or ""),
            normalization_template=str(payload.get("normalization_template") or ""), max_body_bytes=max_body,
            allowed_content_types=allowed_content_types,
            hmac_secret_ref=str(payload.get("hmac_secret_ref") or ""), hmac_header=str(payload.get("hmac_header") or "X-PulseRelay-Signature"),
            hmac_timestamp_header=str(payload.get("hmac_timestamp_header") or "X-PulseRelay-Timestamp"),
            hmac_max_age_seconds=hmac_max_age)
        result = _endpoint_public(store.get_inbound_endpoint(endpoint_id))
        result.update({"secret": secret, "url": f"/v1/hooks/{endpoint_id}/{secret}"})
        return result

    @app.get("/v1/hooks")
    async def list_hooks(request: Request):
        _require_admin_auth(request)
        return {"hooks": [_endpoint_public(endpoint) for endpoint in store.list_inbound_endpoints()]}

    @app.get("/v1/hooks/{endpoint_id}")
    async def get_hook(endpoint_id: str, request: Request):
        _require_admin_auth(request)
        endpoint = store.get_inbound_endpoint(endpoint_id)
        if endpoint is None: raise HTTPException(404, "hook not found")
        return _endpoint_public(endpoint)

    @app.put("/v1/hooks/{endpoint_id}")
    async def update_hook(endpoint_id: str, payload: dict[str, Any], request: Request):
        _require_admin_auth(request)
        endpoint = store.get_inbound_endpoint(endpoint_id)
        if endpoint is None: raise HTTPException(404, "hook not found")
        changes = {key: payload[key] for key in ("name", "enabled", "source_type", "response_template", "normalization_template", "max_body_bytes", "allowed_content_types", "hmac_secret_ref", "hmac_header", "hmac_timestamp_header", "hmac_max_age_seconds") if key in payload}
        new_secret = None
        if payload.get("rotate_secret"):
            new_secret = secrets.token_urlsafe(32)
            changes["secret_hash"] = _hash_secret(new_secret)
        if "max_body_bytes" in changes:
            try: new_max_body = int(changes["max_body_bytes"])
            except (TypeError, ValueError) as exc: raise HTTPException(400, "invalid max_body_bytes") from exc
            if new_max_body <= 0 or new_max_body > 50 * 1024 * 1024: raise HTTPException(400, "max_body_bytes out of range")
        if "allowed_content_types" in changes and (not isinstance(changes["allowed_content_types"], list) or not all(isinstance(item, str) and item.strip() for item in changes["allowed_content_types"])):
            raise HTTPException(400, "allowed_content_types must be a non-empty list of strings")
        if "hmac_max_age_seconds" in changes:
            changes["hmac_max_age_seconds"] = _validate_hmac_age(changes["hmac_max_age_seconds"])
        store.update_inbound_endpoint(endpoint_id, **changes)
        result = _endpoint_public(store.get_inbound_endpoint(endpoint_id))
        if new_secret: result.update({"secret": new_secret, "url": f"/v1/hooks/{endpoint_id}/{new_secret}"})
        return result

    @app.delete("/v1/hooks/{endpoint_id}")
    async def delete_hook(endpoint_id: str, request: Request):
        _require_admin_auth(request)
        if not store.delete_inbound_endpoint(endpoint_id):
            raise HTTPException(404, "hook not found")
        return {"deleted": True, "id": endpoint_id}

    @app.post("/v1/webhooks/{webhook_id}/test")
    async def test_webhook(webhook_id: str, payload: dict[str, Any], request: Request):
        """Send a non-routed diagnostic event to one outbound webhook."""
        _require_admin_auth(request)
        item = store.get_destination(webhook_id)
        if item is None:
            raise HTTPException(404, "webhook not found")
        test_event = payload.get("event") if isinstance(payload.get("event"), dict) else {
            "event": {"type": "pulserelay.webhook_test"},
            "content": {
                "title": "PulseRelay webhook test",
                "text": "This is a test message from PulseRelay.",
            },
            "source": "pulserelay",
        }
        delivery_id = f"test_{secrets.token_hex(8)}"
        result = await WebhookDelivery(
            item, allow_private=bool((item.config or {}).get("allow_private", False))
        ).send(test_event, f"test_{webhook_id}", delivery_id)
        # Diagnostic sends are real outbound requests too, so retain them in
        # the same history as routed deliveries without enqueueing retries.
        store.create_delivery(delivery_id, webhook_id, f"{delivery_id}:test", event_id=None)
        store.update_delivery(
            delivery_id,
            result.status if result.status in {"success", "failed", "skipped"} else "failed",
            http_status=result.http_status,
            error=result.error,
            request_excerpt=result.request_excerpt,
            response_excerpt=result.response_excerpt,
        )
        message_type = str((item.config or {}).get("message_type", "json")).lower()
        content_type = "text/plain; charset=utf-8" if message_type == "text" else "application/json"
        return {
            "ok": result.status == "success",
            "status": result.status,
            "http_status": result.http_status,
            "content_type": content_type,
            "response_excerpt": result.response_excerpt,
            "error": result.error,
            "delivery_id": delivery_id,
        }

    @app.post("/v1/hooks/{endpoint_id}/{secret}")
    @app.post("/v1/webhooks/{endpoint_id}/{secret}")
    async def inbound_hook(endpoint_id: str, secret: str, request: Request):
        endpoint = store.get_inbound_endpoint(endpoint_id)
        if endpoint is None or not endpoint.enabled or not _valid_secret(secret, endpoint.secret_hash):
            raise HTTPException(404, "hook not found")
        if not endpoint.secret_hash.startswith("pbkdf2_sha256$"):
            # Seamlessly upgrade pre-Phase-3 SHA-256 verifiers after a valid
            # request; the old weak verifier is not retained for future calls.
            store.update_inbound_endpoint(endpoint.id, secret_hash=_hash_secret(secret))
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {item.lower() for item in endpoint.allowed_content_types}:
            raise HTTPException(415, "content type not allowed")
        length = request.headers.get("content-length")
        if length:
            try: declared_length = int(length)
            except ValueError: raise HTTPException(400, "invalid content length")
            if declared_length > endpoint.max_body_bytes:
                raise HTTPException(413, "request body too large")
        body = await _read_limited_body(request, endpoint.max_body_bytes)
        if endpoint.hmac_secret_ref:
            timestamp_header = endpoint.hmac_timestamp_header
            timestamp = request.headers.get(timestamp_header)
            try:
                timestamp_value = float(timestamp)
                if not math.isfinite(timestamp_value): raise ValueError
                age = abs(time.time() - timestamp_value)
            except (TypeError, ValueError): raise HTTPException(401, "invalid webhook timestamp")
            if age > endpoint.hmac_max_age_seconds: raise HTTPException(401, "stale webhook signature")
            hmac_secret = os.getenv(endpoint.hmac_secret_ref, "")
            supplied = request.headers.get(endpoint.hmac_header, "")
            expected = hmac.new(hmac_secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
            if supplied.startswith("sha256="): supplied = supplied[7:]
            if not hmac_secret or not hmac.compare_digest(supplied, expected): raise HTTPException(401, "invalid webhook signature")
        try: payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise HTTPException(400, "invalid JSON payload") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "JSON payload must be an object")
        if endpoint.normalization_template:
            try: payload = json.loads(_render(endpoint.normalization_template, event=payload, duplicate=False))
            except Exception as exc:
                store.record_audit("event.normalization_failed", "endpoint", endpoint.id, status="failed", message=str(exc))
                raise HTTPException(422, "normalization failed") from exc
        try:
            normalized_event = ensure_event(payload, source_type=endpoint.source_type)
            if not normalized_event.event.dedupe_key:
                normalized_event.event.dedupe_key = f"hook:{endpoint.id}:{hashlib.sha256(body).hexdigest()}"
            event, duplicate = relay.ingest(normalized_event, source_type=endpoint.source_type)
        except Exception as exc: raise HTTPException(503, "event persistence failed") from exc
        if endpoint.response_template:
            try: return Response(_render(endpoint.response_template, event=event.to_dict(), duplicate=duplicate), media_type="application/json")
            except Exception as exc:
                store.record_audit("hook.response_template_failed", "event", event.id, status="failed", message=str(exc), metadata={"endpoint_id": endpoint.id})
                store.record_dead_letter(event, "hook.response_template", str(exc))
                raise HTTPException(500, "response template failed") from exc
        return JSONResponse({"accepted": True, "event_id": event.id, "duplicate": duplicate})

    @app.get("/v1/events")
    async def list_events(request: Request, limit: int = 100):
        _require_read_auth(request)
        return {"events": [item.payload | {"status": item.status, "received_at": item.created_at} for item in store.list_events(limit=limit)]}

    @app.get("/v1/events/{event_id}")
    async def get_event(event_id: str, request: Request):
        _require_read_auth(request)
        payload = store.get_event_payload(event_id)
        if not payload: raise HTTPException(404, "event not found")
        return payload

    @app.post("/v1/routes")
    async def create_route(payload: dict[str, Any], request: Request):
        _require_admin_auth(request)
        match = payload.get("match") or {}
        source_type = str(match.get("source.type") or str(match.get("event.type", "")).split(".", 1)[0])
        aggregation = payload.get("aggregate", payload.get("aggregation"))
        if aggregation is None and source_type:
            aggregation = _connector_aggregation(source_type)
        store.upsert_route(payload["id"], payload.get("name", payload["id"]), match, aggregation, payload.get("trigger", {}), payload.get("enabled", True), payload.get("destinations", []))
        if source_type:
            _sync_connector_webhooks(store, source_type)
        return {"id": payload["id"]}

    @app.get("/v1/routes")
    async def routes(request: Request):
        _require_admin_auth(request)
        for route in store.list_routes():
            source_type = str(route.match.get("source.type") or str(route.match.get("event.type", "")).split(".", 1)[0])
            if source_type and not route.aggregation:
                store.upsert_route(route.id, route.name, route.match, _connector_aggregation(source_type), route.trigger, route.enabled, store.route_destinations(route.id))
            if source_type:
                _sync_connector_webhooks(store, source_type)
        return {"routes": [route.__dict__ | {"destinations": store.route_destinations(route.id)} for route in store.list_routes()]}

    @app.delete("/v1/routes/{route_id}")
    async def delete_route(route_id: str, request: Request):
        _require_admin_auth(request)
        route = next((item for item in store.list_routes() if item.id == route_id), None)
        if route is None:
            raise HTTPException(404, "route not found")
        source_type = str(route.match.get("source.type") or str(route.match.get("event.type", "")).split(".", 1)[0])
        if not store.delete_route(route_id):
            raise HTTPException(404, "route not found")
        if source_type:
            _sync_connector_webhooks(store, source_type)
        return {"deleted": True, "id": route_id}

    # Public API semantics: /v1/hooks is inbound; /v1/webhooks is outbound.
    @app.post("/v1/webhooks")
    async def create_webhook(payload: dict[str, Any], request: Request):
        _require_admin_auth(request)
        webhook_id = str(payload.get("id") or f"webhook_{secrets.token_hex(8)}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", webhook_id):
            raise HTTPException(400, "invalid webhook id")
        url = str(payload.get("url") or payload.get("target_url") or "").strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            raise HTTPException(400, "webhook url must start with http:// or https://")
        if store.get_destination(webhook_id) and not payload.get("replace"):
            raise HTTPException(409, "webhook already exists")
        store.upsert_destination(webhook_id, str(payload.get("name") or webhook_id), url,
                                 str(payload.get("method") or "POST"), payload.get("config") or {},
                                 bool(payload.get("enabled", True)))
        return {"webhook": store.get_destination(webhook_id).__dict__}

    @app.get("/v1/webhooks")
    async def list_webhooks(request: Request):
        _require_admin_auth(request)
        return {"webhooks": [item.__dict__ for item in store.list_destinations()]}

    @app.get("/v1/webhooks/{webhook_id}/deliveries")
    async def webhook_deliveries(webhook_id: str, request: Request, limit: int = 50):
        _require_admin_auth(request)
        if store.get_destination(webhook_id) is None:
            raise HTTPException(404, "webhook not found")
        bounded_limit = max(1, min(int(limit), 100))
        deliveries = [item.__dict__ for item in store.list_deliveries(bounded_limit)]
        return {"deliveries": [item for item in deliveries if item["destination_id"] == webhook_id]}

    @app.get("/v1/webhooks/{webhook_id}")
    async def get_webhook(webhook_id: str, request: Request):
        _require_admin_auth(request)
        item = store.get_destination(webhook_id)
        if item is None:
            raise HTTPException(404, "webhook not found")
        return {"webhook": item.__dict__}

    @app.put("/v1/webhooks/{webhook_id}")
    async def update_webhook(webhook_id: str, payload: dict[str, Any], request: Request):
        _require_admin_auth(request)
        current = store.get_destination(webhook_id)
        if current is None:
            raise HTTPException(404, "webhook not found")
        url = str(payload.get("url", current.url)).strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            raise HTTPException(400, "webhook url must start with http:// or https://")
        store.upsert_destination(webhook_id, str(payload.get("name", current.name)), url,
                                 str(payload.get("method", current.method)), payload.get("config", current.config),
                                 bool(payload.get("enabled", current.enabled)))
        return {"webhook": store.get_destination(webhook_id).__dict__}

    @app.delete("/v1/webhooks/{webhook_id}")
    async def delete_webhook(webhook_id: str, request: Request):
        _require_admin_auth(request)
        if not store.delete_destination(webhook_id):
            raise HTTPException(404, "webhook not found")
        return {"deleted": True, "id": webhook_id}

    @app.get("/v1/connectors")
    async def connectors(request: Request):
        _require_admin_auth(request)
        result: list[dict[str, Any]] = []
        if not connector_root.is_dir():
            return {"connectors": result}
        known_urls = {item.url for item in store.list_destinations()}
        for item in connector_index.items():
            parsed = item.get("manifest_json", {})
            raw_webhooks = parsed.get("webhooks", []) if isinstance(parsed, dict) else []
            configured = []
            for webhook in raw_webhooks if isinstance(raw_webhooks, list) else []:
                webhook = {"url": webhook} if isinstance(webhook, str) else webhook
                if not isinstance(webhook, dict) or not webhook.get("url"): continue
                url = str(webhook["url"])
                configured.append({"name": str(webhook.get("name") or url), "url": url,
                                   "enabled": bool(webhook.get("enabled", True)), "valid": url in known_urls})
            result.append({**item, "webhooks": configured})
        return {"connectors": result}

    @app.put("/v1/connectors/{connector_id}")
    async def update_connector(connector_id: str, payload: dict[str, Any], request: Request):
        _require_admin_auth(request)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", connector_id):
            raise HTTPException(400, "invalid connector id")
        manifest_path = connector_root / connector_id / "connector.yaml"
        try:
            manifest_path.resolve().relative_to(connector_root.resolve())
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            raise HTTPException(404, "connector manifest not found")
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise HTTPException(400, f"unable to read connector manifest: {exc}")
        if not isinstance(data, dict):
            raise HTTPException(400, "connector manifest must be a mapping")
        current = data.get("aggregation") if isinstance(data.get("aggregation"), dict) else {}
        incoming = payload.get("aggregation", payload)
        if not isinstance(incoming, dict):
            raise HTTPException(400, "aggregation must be an object")
        aggregation = dict(current)
        try:
            if "enabled" in incoming: aggregation["enabled"] = bool(incoming["enabled"])
            if "max_events" in incoming: aggregation["max_events"] = max(0, min(1000, int(incoming["max_events"])))
            if "max_chars" in incoming: aggregation["max_chars"] = max(0, min(200000, int(incoming["max_chars"])))
            if "idle_timeout_seconds" in incoming: aggregation["idle_timeout_seconds"] = max(0.0, min(86400.0, float(incoming["idle_timeout_seconds"])))
            if "max_wait_seconds" in incoming: aggregation["max_wait_seconds"] = max(0.0, min(86400.0, float(incoming["max_wait_seconds"])))
            polling = float(payload.get("polling_interval_seconds", data.get("polling_interval_seconds", data.get("poll_interval_seconds", 60))))
        except (TypeError, ValueError):
            raise HTTPException(400, "aggregation and polling values must be numeric")
        limits = {
            "max_events": aggregation.get("max_events", 0),
            "max_chars": aggregation.get("max_chars", 0),
            "idle_timeout_seconds": aggregation.get("idle_timeout_seconds", 0),
            "max_wait_seconds": aggregation.get("max_wait_seconds", 0),
        }
        active_limits = [name for name, value in limits.items() if float(value or 0) > 0]
        if len(active_limits) > 1:
            raise HTTPException(400, "最多消息数、最多字符数、最大消息间隔、最长等待最多只能有一个大于 0")
        if polling < 5:
            raise HTTPException(400, "polling_interval_seconds must be at least 5 seconds")
        idle = aggregation.get("idle_timeout_seconds")
        if idle and idle <= polling:
            raise HTTPException(400, "idle_timeout_seconds must be greater than polling_interval_seconds")
        data["polling_interval_seconds"] = int(polling) if polling.is_integer() else polling
        data["aggregation"] = aggregation
        temporary = manifest_path.with_suffix(".yaml.tmp")
        try:
            temporary.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            temporary.replace(manifest_path)
        except OSError as exc:
            raise HTTPException(500, f"unable to write connector manifest: {exc}")
        connector_index.refresh(force=True)
        # The worker executes route records, not connector.yaml directly.
        # Propagate the connector setting so disabling aggregation means
        # immediate one-event delivery instead of leaving a stale route policy.
        _sync_connector_aggregation(store, connector_id.replace("-", "_"), aggregation)
        return {"connector": connector_id, "polling_interval_seconds": data["polling_interval_seconds"], "aggregation": aggregation}

    # GBrain MCP administration is deliberately server-side: the browser only
    # presents the PulseRelay admin token, while the GBrain bootstrap credential
    # stays in the PulseRelay process environment.
    @app.get("/v1/mcp/api-keys")
    async def mcp_api_keys(request: Request):
        _require_admin_auth(request)
        return await __import__("asyncio").to_thread(_gbrain_admin_call, "/admin/api/api-keys")

    @app.post("/v1/mcp/api-keys")
    async def create_mcp_api_key(payload: dict[str, Any], request: Request):
        _require_admin_auth(request)
        return await __import__("asyncio").to_thread(_gbrain_admin_call, "/admin/api/api-keys", "POST", payload)

    @app.post("/v1/mcp/api-keys/revoke")
    async def revoke_mcp_api_key(payload: dict[str, Any], request: Request):
        _require_admin_auth(request)
        return await __import__("asyncio").to_thread(_gbrain_admin_call, "/admin/api/api-keys/revoke", "POST", payload)

    @app.post("/v1/mcp/oauth-clients")
    async def create_mcp_oauth_client(payload: dict[str, Any], request: Request):
        _require_admin_auth(request)
        return await __import__("asyncio").to_thread(_gbrain_admin_call, "/admin/api/register-client", "POST", payload)

    @app.post("/v1/mcp/oauth-clients/revoke")
    async def revoke_mcp_oauth_client(payload: dict[str, Any], request: Request):
        _require_admin_auth(request)
        return await __import__("asyncio").to_thread(_gbrain_admin_call, "/admin/api/revoke-client", "POST", payload)

    @app.get("/v1/deliveries")
    async def deliveries(limit: int = 100): return {"deliveries": [item.__dict__ for item in store.list_deliveries(limit)]}

    @app.post("/v1/deliveries/process")
    async def process_delivery():
        result = await relay.process_one()
        return {"processed": result is not None, "status": result.status if result else None}

    @app.post("/v1/batches/flush")
    async def flush(): return {"queued": relay.flush()}

    return app
