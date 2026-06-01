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

    def __init__(self, db_path: str | Path = "data/pulserelay_phase9.db"):
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
            """
        )
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

    def mark_event_failed(self, event_id: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE events SET status = ?, error = ? WHERE event_id = ?",
                ("failed", error, event_id),
            )
            self._conn.commit()

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
