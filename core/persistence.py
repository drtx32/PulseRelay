"""
Phase 9 persistence layer: durable event store, replay, audit, dead-letter.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.event import EventEnvelope, ensure_event


@dataclass
class DeliveryRecord:
    id: str
    event_id: str | None
    batch_id: str | None
    destination_id: str
    idempotency_key: str
    status: str
    attempts: int
    next_attempt_at: str | None
    claimed_by: str | None
    lease_expires_at: str | None
    http_status: int | None
    external_id: str | None
    request_excerpt: str
    response_excerpt: str
    last_error: str
    completed_at: str | None
    created_at: str
    updated_at: str


@dataclass
class BatchRecord:
    id: str
    route_id: str
    group_key: str
    status: str
    event_count: int
    total_chars: int
    first_event_at: str
    last_event_at: str
    idle_deadline_at: str | None
    max_wait_deadline_at: str
    ready_at: str | None


@dataclass
class RouteRecord:
    id: str
    name: str
    enabled: bool
    match: dict[str, Any]
    aggregation: dict[str, Any]
    trigger: dict[str, Any]


@dataclass
class DestinationRecord:
    id: str
    name: str
    enabled: bool
    url: str
    method: str
    config: dict[str, Any]


@dataclass
class CheckpointRecord:
    connector_id: str
    cursor: dict[str, Any]
    last_success_at: str | None
    last_error: str


@dataclass
class InboundEndpointRecord:
    id: str
    name: str
    secret_hash: str
    enabled: bool
    source_type: str
    response_template: str
    normalization_template: str
    max_body_bytes: int
    allowed_content_types: list[str]
    hmac_secret_ref: str
    hmac_header: str
    hmac_timestamp_header: str
    hmac_max_age_seconds: int


@dataclass
class StoredEvent:
    id: int
    event_id: str
    bus_key: str
    source_type: str
    event_type: str
    dedupe_key: str
    status: str
    error: str
    created_at: str
    payload: dict[str, Any]


@dataclass
class DeadLetterRecord:
    id: int
    event_id: str
    source_type: str
    event_type: str
    stage: str
    error: str
    created_at: str
    payload: dict[str, Any]


@dataclass
class AuditLogRecord:
    id: int
    action: str
    entity_type: str
    entity_id: str
    status: str
    message: str
    metadata: dict[str, Any]
    created_at: str


class SQLitePhase9Store:
    """Thread-safe sqlite-backed persistence for phase 9 capabilities."""

    def __init__(self, db_path: str | Path = "data/pulserelay.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                bus_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'received',
                error TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
            CREATE INDEX IF NOT EXISTS idx_events_source_type ON events(source_type);
            CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);

            CREATE TABLE IF NOT EXISTS dead_letters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                stage TEXT NOT NULL,
                error TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_dead_letters_created_at ON dead_letters(created_at);

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT '',
                entity_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ok',
                message TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);

            CREATE TABLE IF NOT EXISTS routes (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                match_json TEXT NOT NULL DEFAULT '{}', aggregation_json TEXT NOT NULL DEFAULT '{}',
                trigger_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS route_destinations (
                route_id TEXT NOT NULL, destination_id TEXT NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (route_id, destination_id)
            );
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, route_id TEXT NOT NULL, group_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'collecting', event_count INTEGER NOT NULL DEFAULT 0,
                total_chars INTEGER NOT NULL DEFAULT 0, first_event_at TEXT NOT NULL, last_event_at TEXT NOT NULL,
                idle_deadline_at TEXT, max_wait_deadline_at TEXT NOT NULL, ready_at TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_batches_ready ON batches(status, idle_deadline_at, max_wait_deadline_at);
            CREATE TABLE IF NOT EXISTS batch_events (
                batch_id TEXT NOT NULL, event_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                PRIMARY KEY(batch_id, event_id)
            );
            CREATE TABLE IF NOT EXISTS destinations (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                url TEXT NOT NULL, method TEXT NOT NULL DEFAULT 'POST', config_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                id TEXT PRIMARY KEY, event_id TEXT, batch_id TEXT, destination_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, claimed_by TEXT,
                claimed_at TEXT, lease_expires_at TEXT, http_status INTEGER, external_id TEXT,
                request_excerpt TEXT NOT NULL DEFAULT '', response_excerpt TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
                completed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_deliveries_claim ON deliveries(status, next_attempt_at, lease_expires_at);
            CREATE TABLE IF NOT EXISTS connector_checkpoints (
                connector_id TEXT PRIMARY KEY, cursor_json TEXT NOT NULL DEFAULT '{}',
                last_success_at TEXT, last_error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inbound_endpoints (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, secret_hash TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, source_type TEXT NOT NULL DEFAULT 'webhook',
                response_template TEXT NOT NULL DEFAULT '', normalization_template TEXT NOT NULL DEFAULT '',
                max_body_bytes INTEGER NOT NULL DEFAULT 1048576, allowed_content_types_json TEXT NOT NULL DEFAULT '["application/json"]',
                hmac_secret_ref TEXT NOT NULL DEFAULT '', hmac_header TEXT NOT NULL DEFAULT 'X-PulseRelay-Signature',
                hmac_timestamp_header TEXT NOT NULL DEFAULT 'X-PulseRelay-Timestamp', hmac_max_age_seconds INTEGER NOT NULL DEFAULT 300,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        # Keep this additive so existing installations retain their delivery
        # history while newer deliveries also retain the sent body excerpt.
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(deliveries)").fetchall()}
        if "request_excerpt" not in columns:
            self._conn.execute("ALTER TABLE deliveries ADD COLUMN request_excerpt TEXT NOT NULL DEFAULT ''")
        self._conn.commit()

    def record_event(
        self,
        bus_key: str,
        event: EventEnvelope,
        status: str = "received",
        error: str = "",
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM events WHERE event_id = ? OR dedupe_key = ? LIMIT 1",
                (event.id, event.dedupe_key),
            ).fetchone()
            if existing:
                return int(existing[0])
            cur = self._conn.execute(
                """
                INSERT INTO events (
                    event_id, bus_key, source_type, event_type, dedupe_key,
                    status, error, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    bus_key,
                    event.source.type,
                    event.event.type,
                    event.dedupe_key,
                    status,
                    error,
                    json.dumps(event.to_dict(), ensure_ascii=False),
                    created_at,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert_route(self, route_id: str, name: str, match: dict[str, Any] | None = None,
                     aggregation: dict[str, Any] | None = None, trigger: dict[str, Any] | None = None,
                     enabled: bool = True, destinations: list[str] | None = None) -> None:
        if isinstance(enabled, list) and destinations is None:
            destinations, enabled = enabled, True
        now = self._now()
        with self._lock:
            self._conn.execute("""INSERT INTO routes(id,name,enabled,match_json,aggregation_json,trigger_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,
                match_json=excluded.match_json,aggregation_json=excluded.aggregation_json,trigger_json=excluded.trigger_json,updated_at=excluded.updated_at""",
                (route_id, name, int(enabled), json.dumps(match or {}), json.dumps(aggregation or {}), json.dumps(trigger or {}), now, now))
            if destinations is not None:
                self._conn.execute("DELETE FROM route_destinations WHERE route_id = ?", (route_id,))
                self._conn.executemany("INSERT INTO route_destinations(route_id,destination_id,ordinal) VALUES(?,?,?)",
                    [(route_id, value, index) for index, value in enumerate(destinations)])
            self._conn.commit()

    def list_routes(self, enabled_only: bool = False) -> list[RouteRecord]:
        query = "SELECT * FROM routes" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(query).fetchall()
        return [RouteRecord(row["id"], row["name"], bool(row["enabled"]), json.loads(row["match_json"]),
                            json.loads(row["aggregation_json"]), json.loads(row["trigger_json"])) for row in rows]

    def route_destinations(self, route_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT destination_id FROM route_destinations WHERE route_id=? ORDER BY ordinal", (route_id,)).fetchall()
        return [row[0] for row in rows]

    def delete_route(self, route_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM routes WHERE id=?", (route_id,))
            self._conn.commit()
            return bool(cur.rowcount)

    def upsert_destination(self, destination_id: str, name: str, url: str, method: str = "POST",
                           config: dict[str, Any] | None = None, enabled: bool = True) -> None:
        now = self._now()
        with self._lock:
            self._conn.execute("""INSERT INTO destinations(id,name,enabled,url,method,config_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,
                url=excluded.url,method=excluded.method,config_json=excluded.config_json,updated_at=excluded.updated_at""",
                (destination_id, name, int(enabled), url, method.upper(), json.dumps(config or {}), now, now))
            self._conn.commit()

    def get_destination(self, destination_id: str) -> DestinationRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM destinations WHERE id=?", (destination_id,)).fetchone()
        return None if row is None else DestinationRecord(row["id"], row["name"], bool(row["enabled"]), row["url"], row["method"], json.loads(row["config_json"]))

    def list_destinations(self) -> list[DestinationRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM destinations ORDER BY name, id").fetchall()
        return [DestinationRecord(row["id"], row["name"], bool(row["enabled"]), row["url"], row["method"], json.loads(row["config_json"])) for row in rows]

    def delete_destination(self, destination_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM destinations WHERE id=?", (destination_id,))
            self._conn.commit()
            return bool(cur.rowcount)

    def create_batch(self, batch_id: str, route_id: str, group_key: str, event: EventEnvelope,
                     idle_deadline_at: str | None, max_wait_deadline_at: str) -> None:
        now = self._now()
        with self._lock:
            self._conn.execute("""INSERT INTO batches(id,route_id,group_key,event_count,total_chars,first_event_at,last_event_at,
                idle_deadline_at,max_wait_deadline_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (batch_id, route_id, group_key, 0, 0, event.event.timestamp, event.event.timestamp,
                 idle_deadline_at, max_wait_deadline_at, now, now))
            self._conn.commit()

    def add_batch_event(self, batch_id: str, event: EventEnvelope, idle_deadline_at: str | None = None) -> bool:
        now = self._now()
        with self._lock:
            cur = self._conn.execute("SELECT COALESCE(MAX(ordinal), -1) + 1 FROM batch_events WHERE batch_id=?", (batch_id,))
            ordinal = int(cur.fetchone()[0])
            inserted = self._conn.execute("INSERT OR IGNORE INTO batch_events(batch_id,event_id,ordinal) VALUES(?,?,?)",
                (batch_id, event.id, ordinal)).rowcount
            if inserted:
                self._conn.execute("UPDATE batches SET event_count=event_count+1,total_chars=total_chars+?,last_event_at=?,idle_deadline_at=?,updated_at=? WHERE id=?",
                    (len(event.content.text), event.event.timestamp, idle_deadline_at, now, batch_id))
            self._conn.commit()
            return bool(inserted)

    def mark_batch_ready(self, batch_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE batches SET status='ready',ready_at=?,updated_at=? WHERE id=? AND status='collecting'", (self._now(), self._now(), batch_id))
            self._conn.commit()

    def get_batch(self, batch_id: str) -> BatchRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if row is None: return None
        return BatchRecord(row["id"], row["route_id"], row["group_key"], row["status"], row["event_count"], row["total_chars"], row["first_event_at"], row["last_event_at"], row["idle_deadline_at"], row["max_wait_deadline_at"], row["ready_at"])

    def list_collecting_batches(self) -> list[BatchRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM batches WHERE status='collecting' ORDER BY created_at").fetchall()
        return [BatchRecord(row["id"], row["route_id"], row["group_key"], row["status"], row["event_count"], row["total_chars"], row["first_event_at"], row["last_event_at"], row["idle_deadline_at"], row["max_wait_deadline_at"], row["ready_at"]) for row in rows]

    def find_collecting_batch(self, route_id: str, group_key: str) -> BatchRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM batches WHERE route_id=? AND group_key=? AND status='collecting' ORDER BY created_at LIMIT 1", (route_id, group_key)).fetchone()
        return None if row is None else BatchRecord(row["id"], row["route_id"], row["group_key"], row["status"], row["event_count"], row["total_chars"], row["first_event_at"], row["last_event_at"], row["idle_deadline_at"], row["max_wait_deadline_at"], row["ready_at"])

    def batch_events(self, batch_id: str) -> list[EventEnvelope]:
        with self._lock:
            rows = self._conn.execute("SELECT e.payload_json FROM events e JOIN batch_events b ON b.event_id=e.event_id WHERE b.batch_id=? ORDER BY b.ordinal", (batch_id,)).fetchall()
        return [ensure_event(json.loads(row[0])) for row in rows]

    def create_delivery(self, delivery_id: str, destination_id: str, idempotency_key: str,
                        event_id: str | None = None, batch_id: str | None = None, next_attempt_at: str | None = None) -> bool:
        now = self._now()
        with self._lock:
            cur = self._conn.execute("""INSERT OR IGNORE INTO deliveries(id,event_id,batch_id,destination_id,idempotency_key,next_attempt_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""", (delivery_id, event_id, batch_id, destination_id, idempotency_key, next_attempt_at or now, now, now))
            self._conn.commit()
            return bool(cur.rowcount)

    def claim_delivery(self, worker_id: str, lease_seconds: int = 60) -> DeliveryRecord | None:
        now = datetime.now(timezone.utc)
        now_s = now.isoformat()
        lease = (now.timestamp() + lease_seconds)
        lease_s = datetime.fromtimestamp(lease, timezone.utc).isoformat()
        with self._lock:
            row = self._conn.execute("""SELECT * FROM deliveries WHERE status IN ('pending','retrying')
                AND (next_attempt_at IS NULL OR next_attempt_at <= ?) AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                ORDER BY created_at LIMIT 1""", (now_s, now_s)).fetchone()
            if row is None: return None
            self._conn.execute("UPDATE deliveries SET status='in_flight',claimed_by=?,claimed_at=?,lease_expires_at=?,attempts=attempts+1,updated_at=? WHERE id=?",
                (worker_id, now_s, lease_s, now_s, row["id"]))
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM deliveries WHERE id=?", (row["id"],)).fetchone()
        return self._delivery_from_row(row)

    @staticmethod
    def _delivery_from_row(row) -> DeliveryRecord:
        return DeliveryRecord(row["id"], row["event_id"], row["batch_id"], row["destination_id"], row["idempotency_key"], row["status"], row["attempts"], row["next_attempt_at"], row["claimed_by"], row["lease_expires_at"], row["http_status"], row["external_id"], row["request_excerpt"], row["response_excerpt"], row["last_error"], row["completed_at"], row["created_at"], row["updated_at"])

    def update_delivery(self, delivery_id: str, status: str, *, http_status: int | None = None,
                        error: str = "", request_excerpt: str = "", response_excerpt: str = "", external_id: str | None = None,
                        next_attempt_at: str | None = None) -> None:
        completed = self._now() if status in {"success", "failed", "dead_letter", "skipped"} else None
        with self._lock:
            self._conn.execute("""UPDATE deliveries SET status=?,http_status=?,last_error=?,request_excerpt=?,response_excerpt=?,external_id=?,
                next_attempt_at=?,completed_at=COALESCE(?,completed_at),claimed_by=NULL,claimed_at=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?""",
                (status, http_status, error, request_excerpt[:20000], response_excerpt[:2000], external_id, next_attempt_at, completed, self._now(), delivery_id))
            self._conn.commit()

    def list_deliveries(self, limit: int = 100, status: str | None = None) -> list[DeliveryRecord]:
        query = "SELECT * FROM deliveries" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC LIMIT ?"
        params = [status, limit] if status else [limit]
        with self._lock: rows = self._conn.execute(query, params).fetchall()
        return [self._delivery_from_row(row) for row in rows]

    def get_checkpoint(self, connector_id: str) -> CheckpointRecord | None:
        with self._lock: row = self._conn.execute("SELECT * FROM connector_checkpoints WHERE connector_id=?", (connector_id,)).fetchone()
        return None if row is None else CheckpointRecord(row["connector_id"], json.loads(row["cursor_json"]), row["last_success_at"], row["last_error"])

    def save_checkpoint(self, connector_id: str, cursor: dict[str, Any], error: str = "") -> None:
        now = self._now()
        with self._lock:
            self._conn.execute("""INSERT INTO connector_checkpoints(connector_id,cursor_json,last_success_at,last_error,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(connector_id) DO UPDATE SET cursor_json=excluded.cursor_json,last_success_at=excluded.last_success_at,last_error=excluded.last_error,updated_at=excluded.updated_at""",
                (connector_id, json.dumps(cursor), None if error else now, error, now))
            self._conn.commit()

    def create_inbound_endpoint(self, endpoint_id: str, name: str, secret_hash: str, *,
                                source_type: str = "webhook", enabled: bool = True,
                                response_template: str = "", normalization_template: str = "",
                                max_body_bytes: int = 1048576,
                                allowed_content_types: list[str] | None = None,
                                hmac_secret_ref: str = "", hmac_header: str = "X-PulseRelay-Signature",
                                hmac_timestamp_header: str = "X-PulseRelay-Timestamp",
                                hmac_max_age_seconds: int = 300) -> None:
        now = self._now()
        with self._lock:
            self._conn.execute("""INSERT INTO inbound_endpoints
                (id,name,secret_hash,enabled,source_type,response_template,normalization_template,max_body_bytes,
                 allowed_content_types_json,hmac_secret_ref,hmac_header,hmac_timestamp_header,hmac_max_age_seconds,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (endpoint_id, name, secret_hash, int(enabled), source_type, response_template, normalization_template,
                 int(max_body_bytes), json.dumps(allowed_content_types or ["application/json"]), hmac_secret_ref,
                 hmac_header, hmac_timestamp_header, int(hmac_max_age_seconds), now, now))
            self._conn.commit()

    def update_inbound_endpoint(self, endpoint_id: str, **changes: Any) -> bool:
        allowed = {"name", "secret_hash", "enabled", "source_type", "response_template",
                   "normalization_template", "max_body_bytes", "allowed_content_types_json",
                   "allowed_content_types", "hmac_secret_ref", "hmac_header", "hmac_timestamp_header", "hmac_max_age_seconds"}
        changes = {key: value for key, value in changes.items() if key in allowed}
        if "allowed_content_types" in changes:
            changes["allowed_content_types_json"] = json.dumps(changes.pop("allowed_content_types"))
        if not changes: return False
        changes["updated_at"] = self._now()
        assignments = ", ".join(f"{key}=?" for key in changes)
        with self._lock:
            cur = self._conn.execute(f"UPDATE inbound_endpoints SET {assignments} WHERE id=?", (*changes.values(), endpoint_id))
            self._conn.commit()
            return bool(cur.rowcount)

    @staticmethod
    def _endpoint_from_row(row) -> InboundEndpointRecord:
        return InboundEndpointRecord(row["id"], row["name"], row["secret_hash"], bool(row["enabled"]), row["source_type"],
            row["response_template"], row["normalization_template"], row["max_body_bytes"], json.loads(row["allowed_content_types_json"]),
            row["hmac_secret_ref"], row["hmac_header"], row["hmac_timestamp_header"], row["hmac_max_age_seconds"])

    def get_inbound_endpoint(self, endpoint_id: str) -> InboundEndpointRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM inbound_endpoints WHERE id=?", (endpoint_id,)).fetchone()
        return None if row is None else self._endpoint_from_row(row)

    def list_inbound_endpoints(self) -> list[InboundEndpointRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM inbound_endpoints ORDER BY id").fetchall()
        return [self._endpoint_from_row(row) for row in rows]

    def delete_inbound_endpoint(self, endpoint_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM inbound_endpoints WHERE id=?", (endpoint_id,))
            self._conn.commit()
            return bool(cur.rowcount)

    def mark_event_failed(self, event_id: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE events SET status = ?, error = ? WHERE event_id = ?",
                ("failed", error, event_id),
            )
            self._conn.commit()

    def get_event_id_by_dedupe(self, dedupe_key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT event_id FROM events WHERE dedupe_key=? LIMIT 1", (dedupe_key,)).fetchone()
        return None if row is None else str(row[0])

    def get_event_payload(self, event_id: str | None) -> dict[str, Any]:
        if not event_id: return {}
        with self._lock:
            row = self._conn.execute("SELECT payload_json FROM events WHERE event_id=? LIMIT 1", (event_id,)).fetchone()
        return {} if row is None else json.loads(row[0])

    def record_dead_letter(
        self,
        event: EventEnvelope | dict[str, Any],
        stage: str,
        error: str,
    ) -> int:
        envelope = ensure_event(event)
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO dead_letters (
                    event_id, source_type, event_type, stage, error, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.id,
                    envelope.source.type,
                    envelope.event.type,
                    stage,
                    error,
                    json.dumps(envelope.to_dict(), ensure_ascii=False),
                    created_at,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def record_audit(
        self,
        action: str,
        entity_type: str = "",
        entity_id: str = "",
        status: str = "ok",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO audit_logs (
                    action, entity_type, entity_id, status, message, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action,
                    entity_type,
                    entity_id,
                    status,
                    message,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    created_at,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_events(
        self,
        limit: int = 100,
        source_type: str | None = None,
        event_type: str | None = None,
        status: str | None = None,
    ) -> list[StoredEvent]:
        where = []
        params: list[Any] = []
        if source_type:
            where.append("source_type = ?")
            params.append(source_type)
        if event_type:
            where.append("event_type = ?")
            params.append(event_type)
        if status:
            where.append("status = ?")
            params.append(status)

        query = """
            SELECT id, event_id, bus_key, source_type, event_type, dedupe_key, status, error, created_at, payload_json
            FROM events
        """
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()

        return [
            StoredEvent(
                id=int(row["id"]),
                event_id=row["event_id"],
                bus_key=row["bus_key"],
                source_type=row["source_type"],
                event_type=row["event_type"],
                dedupe_key=row["dedupe_key"],
                status=row["status"],
                error=row["error"],
                created_at=row["created_at"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def list_dead_letters(self, limit: int = 100) -> list[DeadLetterRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, event_id, source_type, event_type, stage, error, payload_json, created_at
                FROM dead_letters
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            DeadLetterRecord(
                id=int(row["id"]),
                event_id=row["event_id"],
                source_type=row["source_type"],
                event_type=row["event_type"],
                stage=row["stage"],
                error=row["error"],
                created_at=row["created_at"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def list_audit_logs(
        self,
        limit: int = 100,
        action: str | None = None,
        status: str | None = None,
    ) -> list[AuditLogRecord]:
        where = []
        params: list[Any] = []
        if action:
            where.append("action = ?")
            params.append(action)
        if status:
            where.append("status = ?")
            params.append(status)

        query = """
            SELECT id, action, entity_type, entity_id, status, message, metadata_json, created_at
            FROM audit_logs
        """
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()

        return [
            AuditLogRecord(
                id=int(row["id"]),
                action=row["action"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                status=row["status"],
                message=row["message"],
                metadata=json.loads(row["metadata_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def replay_events(
        self,
        limit: int = 100,
        source_type: str | None = None,
        event_type: str | None = None,
        status: str | None = "received",
    ) -> list[EventEnvelope]:
        stored = self.list_events(
            limit=limit,
            source_type=source_type,
            event_type=event_type,
            status=status,
        )
        # Replay old->new ordering.
        return [ensure_event(item.payload) for item in reversed(stored)]

    def stats(self) -> dict[str, int]:
        with self._lock:
            events_total = int(self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            events_failed = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM events WHERE status = 'failed'"
                ).fetchone()[0]
            )
            dead_letters_total = int(
                self._conn.execute("SELECT COUNT(*) FROM dead_letters").fetchone()[0]
            )
            audit_logs_total = int(
                self._conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
            )

        return {
            "events_total": events_total,
            "events_failed": events_failed,
            "dead_letters_total": dead_letters_total,
            "audit_logs_total": audit_logs_total,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
