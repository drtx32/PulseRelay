"""The single outbound transport contract for PulseRelay."""
from __future__ import annotations

import asyncio
import hashlib
import http.client
import hmac
import ipaddress
import json
import os
import socket
import ssl
import time
from urllib.parse import urlsplit
from dataclasses import dataclass
from typing import Any
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from core.event import EventEnvelope
from core.persistence import DestinationRecord


@dataclass
class WebhookResult:
    status: str
    http_status: int | None = None
    external_id: str = ""
    response_excerpt: str = ""
    error: str = ""
    emitted_event: dict[str, Any] | None = None
    retry_after: float | None = None
    request_excerpt: str = ""


def stable_idempotency_key(subject_id: str, destination_id: str, payload_version: str = "v1") -> str:
    return f"{subject_id}:{destination_id}:{payload_version}"


def _resolve(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1], "")
    return value


def _private_host(url: str) -> bool:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return True
        addresses = _resolve_addresses(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
        return not addresses or any(not address.is_global for address in addresses)
    except (OSError, ValueError):
        return True


def _resolve_addresses(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        literal = ipaddress.ip_address(host)
        return [literal]
    except ValueError:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = {ipaddress.ip_address(record[4][0].split("%", 1)[0]) for record in records}
        if not addresses:
            raise OSError("destination host did not resolve")
        return sorted(addresses, key=lambda value: (value.version, int(value)))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, address: str, port: int, server_hostname: str, timeout: float):
        super().__init__(address, port, timeout=timeout, context=ssl.create_default_context())
        self._server_hostname = server_hostname

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._server_hostname)


def _safe_http_request(url: str, method: str, headers: dict[str, str], body: bytes,
                       timeout: float, allow_private: bool = False):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("destination URL rejected")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = _resolve_addresses(parts.hostname, port)
    if not allow_private and any(not address.is_global for address in addresses):
        raise ValueError("destination URL rejected")
    address = str(addresses[0])
    host_header = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    if parts.port:
        host_header = f"{host_header}:{parts.port}"
    request_headers = dict(headers)
    request_headers.setdefault("Host", host_header)
    connection = (
        _PinnedHTTPSConnection(address, port, parts.hostname, timeout)
        if parts.scheme == "https" else http.client.HTTPConnection(address, port, timeout=timeout)
    )
    try:
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        return response.status, response.read(2000).decode("utf-8", errors="replace"), response.getheader("Retry-After")
    finally:
        connection.close()


def _template_content(subject: dict[str, Any]) -> Any:
    content = subject.get("content") or {}
    return content.get("text", content) if isinstance(content, dict) else content


def _replace_input_content(template: str, content: Any) -> str:
    """Replace the documented token without corrupting surrounding JSON.

    The UI presents the token inside a JSON string most of the time. Replace
    the quoted token as a complete JSON value first; the second replacement
    keeps unquoted/raw templates working as well.
    """
    encoded = json.dumps(content, ensure_ascii=False)
    return template.replace('"${input content}"', encoded).replace("'${input content}'", encoded).replace("${input content}", encoded)


class WebhookDelivery:
    def __init__(self, destination: DestinationRecord, allow_private: bool = False):
        self.destination = destination
        self.allow_private = allow_private

    async def send(self, subject: dict[str, Any], subject_id: str, delivery_id: str,
                   aggregation: dict[str, Any] | None = None) -> WebhookResult:
        if not self.destination.enabled: return WebhookResult("skipped")
        url = _resolve(self.destination.url)
        if not url.startswith(("http://", "https://")) or (not self.allow_private and _private_host(url)):
            return WebhookResult("failed", error="destination URL rejected")
        config = self.destination.config or {}
        headers = {str(k): str(_resolve(v)) for k, v in (config.get("headers") or {}).items()}
        # A Route may override the destination presentation for aggregated
        # batches.  Single-event deliveries continue to use the Webhook's
        # own template; the two concerns must not be coupled.
        presentation = aggregation if isinstance(aggregation, dict) else config
        message_type = str(presentation.get("message_type", config.get("message_type", "json"))).lower()
        template = str(presentation.get("message_template", config.get("message_template", "")))
        if message_type == "text":
            if template:
                content = _template_content(subject)
                if "${input content}" in template:
                    candidate = _replace_input_content(template, content)
                    try:
                        json.loads(candidate)
                    except json.JSONDecodeError:
                        body_value = template.replace("${input content}", str(content))
                        content_type = "text/plain; charset=utf-8"
                    else:
                        # A text-labelled webhook can still target a provider
                        # such as WeCom whose body is a JSON envelope.
                        body = candidate.encode("utf-8")
                        content_type = "application/json"
                        candidate = None
                        body_value = None
                else:
                    environment = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined, cache_size=0)
                    environment.globals.clear(); environment.filters.clear(); environment.tests.clear()
                    body_value = environment.from_string(template).render(event=subject, input=subject.get("content", {}))
                    content_type = "text/plain; charset=utf-8"
                if body_value is not None:
                    body = body_value.encode("utf-8")
            else:
                body_value = str((subject.get("content") or {}).get("text", ""))
                body = body_value.encode("utf-8")
                content_type = "text/plain; charset=utf-8"
        else:
            if template:
                body = _replace_input_content(template, _template_content(subject)).encode("utf-8")
            else:
                body = json.dumps(subject, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        headers.update({"Content-Type": content_type, "X-PulseRelay-Event-ID": subject_id,
                        "X-PulseRelay-Delivery-ID": delivery_id, "X-PulseRelay-Timestamp": str(int(time.time())),
                        "Idempotency-Key": stable_idempotency_key(subject_id, self.destination.id, str(config.get("payload_version", "v1")))})
        signing_secret = _resolve(config.get("hmac_secret"))
        if signing_secret:
            headers["X-PulseRelay-Signature"] = hmac.new(str(signing_secret).encode(), body, hashlib.sha256).hexdigest()
        timeout = float(config.get("timeout_seconds", 30))
        try:
            status_code, text, retry_after_header = await asyncio.to_thread(
                _safe_http_request, url, self.destination.method, headers, body, timeout, self.allow_private
            )
            emitted = None
            try:
                parsed = json.loads(text) if text else {}
                emitted = parsed.get("emit") if isinstance(parsed, dict) else None
            except json.JSONDecodeError: pass
            retry_after = None
            try: retry_after = float(retry_after_header)
            except (TypeError, ValueError): pass
            if 300 <= status_code < 400:
                return WebhookResult("failed", status_code, error="redirect rejected", response_excerpt=text,
                                     request_excerpt=body.decode("utf-8", errors="replace"))
            return WebhookResult("success" if status_code in set(config.get("success_statuses", [200, 201, 202, 204])) else "failed", status_code, response_excerpt=text, emitted_event=emitted, retry_after=retry_after, request_excerpt=body.decode("utf-8", errors="replace"))
        except Exception as exc:
            status_code = getattr(exc, "code", None)
            text = ""
            if isinstance(exc, http.client.HTTPException):
                text = str(exc)
            elif isinstance(exc, OSError) and getattr(exc, "status", None):
                status_code = exc.status
            if status_code and 300 <= status_code < 400:
                return WebhookResult("failed", status_code, error="redirect rejected", response_excerpt=text,
                                     request_excerpt=body.decode("utf-8", errors="replace"))
            return WebhookResult("failed", http_status=status_code, error="destination request failed",
                                 response_excerpt=text, request_excerpt=body.decode("utf-8", errors="replace"))
