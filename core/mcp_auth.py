"""PulseRelay MCP authentication and scope engine.

API keys and OAuth access tokens resolve to the same Principal model. Secrets
are never stored in plaintext. The existing Web UI contract is preserved:
API keys created without an explicit scope are full-control credentials, while
OAuth accepts the GBrain-style `read`, `write`, and `admin` scope aliases plus
fine-grained PulseRelay scopes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

FINE_SCOPES = {
    "connectors:read", "connectors:write",
    "routes:read", "routes:write",
    "deliveries:read", "deliveries:replay",
    "events:read", "admin",
}
SCOPE_ALIASES = {
    "read": {"connectors:read", "routes:read", "deliveries:read", "events:read"},
    "write": {"connectors:read", "connectors:write", "routes:read", "routes:write",
              "deliveries:read", "deliveries:replay", "events:read"},
    "admin": {"admin"},
}
ALL_SCOPES = FINE_SCOPES | set(SCOPE_ALIASES)


@dataclass(frozen=True)
class Principal:
    subject: str
    auth_type: str
    scopes: frozenset[str]

    def allows(self, required: str) -> bool:
        return "admin" in self.scopes or required in self.scopes


current_principal: ContextVar[Principal | None] = ContextVar("pulserelay_mcp_principal", default=None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_scopes(scopes: Iterable[str] | str | None, *, default: Iterable[str] | None = None) -> list[str]:
    if scopes is None or scopes == "" or scopes == []:
        values = list(default or [])
    elif isinstance(scopes, str):
        values = scopes.replace(",", " ").split()
    else:
        values = [str(item).strip() for item in scopes]
    expanded: set[str] = set()
    unknown: list[str] = []
    for value in values:
        if not value:
            continue
        if value in SCOPE_ALIASES:
            expanded.update(SCOPE_ALIASES[value])
        elif value in FINE_SCOPES:
            expanded.add(value)
        else:
            unknown.append(value)
    if unknown:
        raise ValueError(f"unknown scopes: {', '.join(sorted(set(unknown)))}")
    return sorted(expanded)


class MCPAuthStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS mcp_api_keys (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, secret_hash TEXT NOT NULL UNIQUE,
                    prefix TEXT NOT NULL, scopes_json TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
                    client_id TEXT PRIMARY KEY, name TEXT NOT NULL, client_secret_hash TEXT NOT NULL,
                    scopes_json TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS mcp_oauth_tokens (
                    token_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, scopes_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_mcp_tokens_client ON mcp_oauth_tokens(client_id);
            """)
            self._conn.commit()

    def create_api_key(self, name: str, scopes: Iterable[str] | str | None = None) -> dict:
        # Existing UI has no API-key scope control. Preserve its semantics by
        # granting full control unless a caller explicitly supplies scopes.
        scopes_n = normalize_scopes(scopes, default=["admin"])
        key_id = "key_" + secrets.token_hex(8)
        secret = "prk_" + secrets.token_urlsafe(32)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO mcp_api_keys(id,name,secret_hash,prefix,scopes_json,created_at) VALUES(?,?,?,?,?,?)",
                (key_id, name, _hash(secret), secret[:12], json.dumps(scopes_n), now),
            )
            self._conn.commit()
        return {"id": key_id, "name": name, "api_key": secret, "prefix": secret[:12], "scopes": scopes_n, "created_at": now}

    def list_api_keys(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT id,name,prefix,scopes_json,enabled,created_at,revoked_at FROM mcp_api_keys ORDER BY created_at DESC").fetchall()
        return [{"id": r["id"], "name": r["name"], "prefix": r["prefix"], "scopes": json.loads(r["scopes_json"]),
                 "status": "active" if r["enabled"] and not r["revoked_at"] else "revoked",
                 "enabled": bool(r["enabled"]), "created_at": r["created_at"], "revoked_at": r["revoked_at"]} for r in rows]

    def revoke_api_key(self, key_id: str | None = None, *, name: str | None = None) -> bool:
        if not key_id and not name:
            return False
        column, value = ("id", key_id) if key_id else ("name", name)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE mcp_api_keys SET enabled=0,revoked_at=? WHERE {column}=? AND enabled=1",
                (_now(), value),
            )
            self._conn.commit()
            return bool(cur.rowcount)

    def create_oauth_client(self, name: str, scopes: Iterable[str] | str | None = None) -> dict:
        scopes_n = normalize_scopes(scopes, default=["read"])
        client_id = "prc_" + secrets.token_urlsafe(18)
        client_secret = "prs_" + secrets.token_urlsafe(32)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO mcp_oauth_clients(client_id,name,client_secret_hash,scopes_json,created_at) VALUES(?,?,?,?,?)",
                (client_id, name, _hash(client_secret), json.dumps(scopes_n), now),
            )
            self._conn.commit()
        return {"client_id": client_id, "client_secret": client_secret, "name": name, "scopes": scopes_n,
                "grant_types": ["client_credentials"], "token_endpoint_auth_method": "client_secret_basic",
                "created_at": now}

    def list_oauth_clients(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT client_id,name,scopes_json,enabled,created_at,revoked_at FROM mcp_oauth_clients ORDER BY created_at DESC").fetchall()
        return [{"client_id": r["client_id"], "name": r["name"], "scopes": json.loads(r["scopes_json"]),
                 "status": "active" if r["enabled"] and not r["revoked_at"] else "revoked",
                 "enabled": bool(r["enabled"]), "created_at": r["created_at"], "revoked_at": r["revoked_at"]} for r in rows]

    def revoke_oauth_client(self, client_id: str) -> bool:
        now = _now()
        with self._lock:
            cur = self._conn.execute("UPDATE mcp_oauth_clients SET enabled=0,revoked_at=? WHERE client_id=? AND enabled=1", (now, client_id))
            self._conn.execute("UPDATE mcp_oauth_tokens SET revoked_at=? WHERE client_id=? AND revoked_at IS NULL", (now, client_id))
            self._conn.commit()
            return bool(cur.rowcount)

    def issue_client_credentials_token(self, client_id: str, client_secret: str,
                                       requested_scopes: Iterable[str] | str | None = None,
                                       ttl_seconds: int = 3600) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM mcp_oauth_clients WHERE client_id=? AND enabled=1 AND revoked_at IS NULL", (client_id,)).fetchone()
        if row is None or not hmac.compare_digest(row["client_secret_hash"], _hash(client_secret)):
            return None
        allowed = set(json.loads(row["scopes_json"]))
        requested = set(normalize_scopes(requested_scopes)) if requested_scopes else allowed
        if not requested.issubset(allowed):
            raise ValueError("requested scope exceeds client grant")
        ttl_seconds = max(60, min(int(ttl_seconds), 86400))
        token = "pra_" + secrets.token_urlsafe(36)
        now_dt = datetime.now(timezone.utc)
        expires = now_dt + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._conn.execute(
                "INSERT INTO mcp_oauth_tokens(token_hash,client_id,scopes_json,expires_at,created_at) VALUES(?,?,?,?,?)",
                (_hash(token), client_id, json.dumps(sorted(requested)), expires.isoformat(), now_dt.isoformat()),
            )
            self._conn.commit()
        return {"access_token": token, "token_type": "Bearer", "expires_in": ttl_seconds,
                "scope": " ".join(sorted(requested))}

    def authenticate_bearer(self, token: str) -> Principal | None:
        admin = os.getenv("PULSERELAY_ADMIN_TOKEN", "").strip()
        if admin and hmac.compare_digest(token, admin):
            return Principal("bootstrap-admin", "admin_token", frozenset({"admin"}))
        digest = _hash(token)
        with self._lock:
            row = self._conn.execute("SELECT id,scopes_json FROM mcp_api_keys WHERE secret_hash=? AND enabled=1 AND revoked_at IS NULL", (digest,)).fetchone()
            if row is not None:
                return Principal(row["id"], "api_key", frozenset(json.loads(row["scopes_json"])))
            row = self._conn.execute("SELECT client_id,scopes_json,expires_at FROM mcp_oauth_tokens WHERE token_hash=? AND revoked_at IS NULL", (digest,)).fetchone()
        if row is not None and datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc):
            return Principal(row["client_id"], "oauth", frozenset(json.loads(row["scopes_json"])))
        return None


_auth_stores: dict[str, MCPAuthStore] = {}
_auth_lock = threading.Lock()


def get_auth_store(db_path: str | Path | None = None) -> MCPAuthStore:
    path = str(Path(db_path or os.getenv("PULSERELAY_DB_PATH", "data/pulserelay.db")).resolve())
    with _auth_lock:
        if path not in _auth_stores:
            _auth_stores[path] = MCPAuthStore(path)
        return _auth_stores[path]


def require_scope(scope: str) -> Principal:
    principal = current_principal.get()
    if principal is None:
        raise PermissionError("authentication context unavailable")
    if not principal.allows(scope):
        raise PermissionError(f"missing scope: {scope}")
    return principal
