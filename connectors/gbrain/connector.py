"""GBrain HTTP MCP polling connector.

This connector is intentionally standalone so it can run from the mounted
``connectors`` volume.  It uses the MCP Streamable HTTP JSON-RPC transport and
keeps the GBrain tool names/arguments configurable because deployments may
expose different task-query schemas.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from fnmatch import fnmatchcase
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


MCP_URL = os.environ.get("GBRAIN_MCP_URL", "").strip()
MCP_TOKEN = os.environ.get("GBRAIN_MCP_TOKEN", "").strip()
if MCP_TOKEN.lower().startswith("bearer "):
    # Accept both the documented raw token and a copied Authorization value.
    MCP_TOKEN = MCP_TOKEN[7:].strip()
OAUTH_CLIENT_ID = os.environ.get("GBRAIN_CLIENT_ID", "").strip()
OAUTH_CLIENT_SECRET = os.environ.get("GBRAIN_CLIENT_SECRET", "").strip()
OAUTH_SCOPE = os.environ.get("GBRAIN_OAUTH_SCOPE", "read").strip()
OAUTH_TOKEN_URL = os.environ.get("GBRAIN_OAUTH_TOKEN_URL", "").strip()
if not OAUTH_TOKEN_URL and MCP_URL:
    parts = urlsplit(MCP_URL)
    OAUTH_TOKEN_URL = f"{parts.scheme}://{parts.netloc}/token"
LIST_RUNS_TOOL = os.environ.get("GBRAIN_LIST_RUNS_TOOL", "list_pages")
GET_OUTPUT_TOOL = os.environ.get("GBRAIN_GET_OUTPUT_TOOL", "get_page")
INGEST_URL = os.environ.get("PULSERELAY_INGEST_URL", "").strip()
INTERVAL = max(5, int(os.environ.get("POLL_INTERVAL_SECONDS", "60")))
STATE_DIR = Path(os.environ.get("PULSERELAY_STATE_DIR", ".state"))
STATE_FILE = STATE_DIR / "checkpoint.json"
WATCH_PATHS = json.loads(os.environ.get("PULSERELAY_WATCH_PATHS_JSON", "[]"))
IGNORE_PATHS = json.loads(os.environ.get("PULSERELAY_IGNORE_PATHS_JSON", "[]"))


def path_is_watched(path: str) -> bool:
    """Match normalized output slugs against connector.yaml path patterns."""
    normalized = path.strip("/")
    if any(fnmatchcase(normalized, pattern.strip("/")) for pattern in IGNORE_PATHS):
        return False
    return not WATCH_PATHS or any(fnmatchcase(normalized, pattern.strip("/")) for pattern in WATCH_PATHS)


def _json_env(name: str, default: dict[str, Any]) -> dict[str, Any]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return dict(default)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value


def _jsonrpc_result(payload: Any) -> Any:
    """Unwrap common MCP JSON and text-content result shapes."""
    if isinstance(payload, dict) and "error" in payload:
        raise RuntimeError(f"MCP error: {payload['error']}")
    if isinstance(payload, dict) and "result" in payload:
        return _jsonrpc_result(payload["result"])
    if isinstance(payload, dict) and "structuredContent" in payload:
        return _jsonrpc_result(payload["structuredContent"])
    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        values = []
        for item in payload["content"]:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                try:
                    values.append(json.loads(text))
                except json.JSONDecodeError:
                    values.append(text)
        if len(values) == 1:
            return values[0]
        return values
    return payload


def _runs_from_result(value: Any) -> list[dict[str, Any]]:
    value = _jsonrpc_result(value)
    if isinstance(value, list):
        return [_normalize_run_shape(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("runs", "items", "results", "pages", "data"):
            if key in value:
                return _runs_from_result(value[key])
    return []


def _normalize_run_shape(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten common list_pages envelopes while preserving original fields."""
    result = dict(item)
    for key in ("frontmatter", "metadata", "fields"):
        nested = result.get(key)
        if isinstance(nested, dict):
            merged = dict(nested)
            merged.update(result)
            result = merged
    if not result.get("id") and result.get("slug"):
        result["id"] = result["slug"]
    if not result.get("path") and result.get("slug"):
        result["path"] = result["slug"]
    return result


def _output_text(value: Any) -> str:
    value = _jsonrpc_result(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "body", "markdown", "compiled_truth", "output"):
            if key in value:
                return _output_text(value[key])
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class MCPHTTPClient:
    """Small dependency-free MCP Streamable HTTP client."""

    def __init__(self, url: str, token: str = "", client_id: str = "", client_secret: str = "",
                 token_url: str = "", scope: str = "read", timeout: float = 30):
        if not url or (not token and not (client_id and client_secret and token_url)):
            raise RuntimeError("configure GBRAIN_MCP_TOKEN or GBRAIN_CLIENT_ID/GBRAIN_CLIENT_SECRET")
        self.url, self.token = url, token
        self.client_id, self.client_secret = client_id, client_secret
        self.token_url, self.scope, self.timeout = token_url, scope, timeout
        self._access_token, self._access_token_expires_at = "", 0.0
        self._request_id = 0
        self._session_id: str | None = None
        self._initialized = False

    def _post(self, method: str, params: dict[str, Any] | None = None, *, expect_response: bool = True) -> Any:
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if expect_response:
            payload["id"] = self._request_id
        if params is not None:
            payload["params"] = params
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                   "Authorization": f"Bearer {self._get_access_token()}"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = Request(self.url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id
            raw = response.read(4 * 1024 * 1024).decode("utf-8")
        if not expect_response:
            return {}
        for line in raw.splitlines():
            if line.startswith("data:"):
                candidate = line[5:].strip()
                if candidate:
                    return json.loads(candidate)
        return json.loads(raw) if raw.strip() else {}

    def _get_access_token(self) -> str:
        if self.token:
            return self.token
        if self._access_token and time.time() < self._access_token_expires_at - 60:
            return self._access_token
        form = {"grant_type": "client_credentials", "client_id": self.client_id,
                "client_secret": self.client_secret}
        if self.scope:
            form["scope"] = self.scope
        request = Request(self.token_url, data=urlencode(form).encode("utf-8"),
                          headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded",
                                   "User-Agent": "PulseRelay-gbrain/1.0"}, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise RuntimeError("OAuth token response did not contain access_token")
        self._access_token = access_token
        self._access_token_expires_at = time.time() + max(60, int(payload.get("expires_in", 3600)))
        return access_token

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._post("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                   "clientInfo": {"name": "pulserelay-gbrain", "version": "1.0"}})
        # MCP initialization notification has no response requirement.
        self._post("notifications/initialized", {}, expect_response=False)
        self._initialized = True

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self._ensure_initialized()
        return self._post("tools/call", {"name": name, "arguments": arguments})

    def list_runs(self, *, after: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        arguments = _json_env("GBRAIN_LIST_RUNS_ARGS_JSON", {"type": "task_run", "sort": "updated_asc", "limit": 100})
        if after:
            arguments.setdefault("after", after)
        return _runs_from_result(self.call_tool(LIST_RUNS_TOOL, arguments))

    def get_output(self, slug: str) -> Any:
        arguments = _json_env("GBRAIN_GET_OUTPUT_ARGS_JSON", {})
        arguments.setdefault("slug", slug)
        return _jsonrpc_result(self.call_tool(GET_OUTPUT_TOOL, arguments))

    def get_run_details(self, run: dict[str, Any]) -> dict[str, Any]:
        """Read task-run frontmatter from the run page.

        ``list_pages`` returns an index entry, not the task-run document.  The
        completion and output metadata therefore lives in the page's
        ``frontmatter`` and must be read with a second MCP call.
        """
        slug = str(run.get("slug") or run.get("path") or run.get("id") or "")
        if not slug:
            return dict(run)
        page = self.get_output(slug)
        if not isinstance(page, dict):
            return dict(run)
        frontmatter = page.get("frontmatter")
        if not isinstance(frontmatter, dict):
            return dict(run)
        enriched = dict(run)
        enriched.update(frontmatter)
        # Keep the canonical connector names while accepting the GBrain page
        # field name used by task runs.
        if "verification_status" in frontmatter:
            enriched["verification"] = frontmatter["verification_status"]
        if "output_slug" in frontmatter:
            enriched["output_slug"] = frontmatter["output_slug"]
        return enriched


def load_checkpoint() -> dict[str, Any]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_checkpoint(checkpoint: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def _run_key(run: dict[str, Any]) -> tuple[str, str]:
    return (str(run.get("updated_at") or run.get("completed_at") or run.get("finished_at") or ""),
            str(run.get("id") or run.get("run_id") or run.get("path") or ""))


def normalize_run(run: dict[str, Any], text: str) -> dict[str, Any]:
    run_id = str(run.get("id") or run.get("run_id") or run.get("path") or "")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    timestamp = str(run.get("completed_at") or run.get("updated_at") or datetime.now(timezone.utc).isoformat())
    return {
        "id": f"gbrain:{run_id}:{content_hash[:16]}",
        "source": {"type": "gbrain", "id": run_id, "name": "GBrain"},
        "sender": {"id": "gbrain", "name": "GBrain", "role": "system", "trust_level": "system"},
        "event": {"type": "gbrain.output.ready", "action": "publish", "timestamp": timestamp,
                  "dedupe_key": f"gbrain:{run_id}:{content_hash}"},
        "content": {"title": str(run.get("title") or "GBrain output ready"), "text": text,
                    "raw": {"source_run": run_id, "output_slug": run.get("output_slug"), "content_hash": content_hash}},
        "context": {"project_id": str(run.get("project_id", ""))},
        "routing": {"priority": "normal", "labels": ["gbrain", "publish"]},
    }


def publish(event: dict[str, Any]) -> None:
    request = Request(INGEST_URL, data=json.dumps(event, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=30) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"PulseRelay rejected event with HTTP {response.status}")


def poll_once(client: MCPHTTPClient) -> int:
    if not INGEST_URL:
        raise RuntimeError("PULSERELAY_INGEST_URL is required")
    checkpoint = load_checkpoint()
    cursor = checkpoint.get("cursor") if isinstance(checkpoint.get("cursor"), dict) else {}
    runs = sorted(client.list_runs(after=cursor), key=_run_key)
    seen = {str(item) for item in checkpoint.get("seen", [])}
    emitted = 0
    next_cursor = cursor
    cursor_key = (str(cursor.get("updated_at", "")), str(cursor.get("tie_breaker", "")))
    for indexed_run in runs:
        # list_pages is sorted ascending.  Once a cursor exists, avoid
        # re-reading old task-run pages on every polling cycle.
        if cursor and _run_key(indexed_run) <= cursor_key:
            continue
        run = client.get_run_details(indexed_run)
        if str(run.get("status", "")).lower() != "completed" or str(run.get("verification", "")).lower() != "passed":
            continue
        slug = run.get("output_slug")
        if not slug or not path_is_watched(str(slug)):
            continue
        text = _output_text(client.get_output(str(slug)))
        if not text:
            continue
        event = normalize_run(run, text)
        if event["event"]["dedupe_key"] in seen:
            next_cursor = {"updated_at": _run_key(run)[0], "tie_breaker": _run_key(run)[1]}
            continue
        publish(event)
        seen.add(event["event"]["dedupe_key"])
        emitted += 1
        next_cursor = {"updated_at": _run_key(run)[0], "tie_breaker": _run_key(run)[1]}
    save_checkpoint({"cursor": next_cursor, "seen": list(seen)[-1000:],
                     "last_success_at": datetime.now(timezone.utc).isoformat()})
    return emitted


def main() -> None:
    client = MCPHTTPClient(MCP_URL, MCP_TOKEN, OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET,
                           OAUTH_TOKEN_URL, OAUTH_SCOPE)
    print(f"Watching GBrain MCP via {MCP_URL}; interval={INTERVAL}s", flush=True)
    while True:
        try:
            print(f"Published {poll_once(client)} new GBrain event(s)", flush=True)
        except Exception as exc:
            print(f"poll failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
