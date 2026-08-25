"""Durable, route-scoped event aggregation."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from core.event import EventEnvelope
from core.persistence import SQLitePhase9Store, BatchRecord, RouteRecord
from core.routing import get_field

DEFAULT_MAX_WAIT_SECONDS = 300.0
MAX_EVENTS_LIMIT = 1000
MAX_CHARS_LIMIT = 200_000
MAX_TIMEOUT_SECONDS = 86_400.0


def validate_aggregation_policy(policy: dict | None) -> dict:
    """Validate and normalize the connector aggregation settings."""
    if policy is None:
        return {}
    if not isinstance(policy, dict):
        raise ValueError("aggregation must be an object")

    def number(name: str, *, integer: bool = False, maximum: float) -> int | float:
        value = policy.get(name, 0)
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a number")
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a number") from exc
        minimum = 1 if name == "max_events" else 0
        if not math.isfinite(parsed) or parsed < minimum:
            raise ValueError(f"{name} must be greater than or equal to {minimum}")
        if parsed > maximum:
            raise ValueError(f"{name} must be at most {maximum:g}")
        if integer and not parsed.is_integer():
            raise ValueError(f"{name} must be an integer")
        return int(parsed) if integer else parsed

    normalized = dict(policy)
    normalized["max_events"] = number("max_events", integer=True, maximum=MAX_EVENTS_LIMIT)
    normalized["max_chars"] = number("max_chars", integer=True, maximum=MAX_CHARS_LIMIT)
    normalized["idle_timeout_seconds"] = number("idle_timeout_seconds", maximum=MAX_TIMEOUT_SECONDS)
    normalized["max_wait_seconds"] = number("max_wait_seconds", maximum=MAX_TIMEOUT_SECONDS)

    if normalized.get("enabled", True) is not False and normalized["max_events"] > 1:
        if normalized["idle_timeout_seconds"] <= 0 and normalized["max_wait_seconds"] <= 0:
            raise ValueError("消息数大于 1 时，最大消息间隔和最长等待至少一个必须大于 0")
    return normalized


def _parse(value: str | None) -> datetime | None:
    if not value: return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Aggregator:
    def __init__(self, store: SQLitePhase9Store):
        self.store = store

    def group_key(self, event: EventEnvelope, route: RouteRecord) -> str:
        fields = route.aggregation.get("group_by") or route.match.get("group_by") or []
        if isinstance(fields, str): fields = [fields]
        values = [get_field(event, field) for field in fields]
        return json.dumps(values, sort_keys=True, ensure_ascii=False) if values else "_"

    def add(self, event: EventEnvelope, route: RouteRecord) -> BatchRecord:
        policy = route.aggregation or {}
        group = self.group_key(event, route)
        candidates = self.store.find_collecting_batch(route.id, group)
        now = datetime.now(timezone.utc)
        active_limits = [
            float(policy.get("max_events", 0) or 0),
            float(policy.get("max_chars", 0) or 0),
            float(policy.get("idle_timeout_seconds", 0) or 0),
            float(policy.get("max_wait_seconds", 0) or 0),
        ]
        immediate = policy.get("enabled") is False or not policy or not any(value > 0 for value in active_limits)
        if candidates and not immediate:
            batch = candidates
        else:
            max_wait_value = float(policy.get("max_wait_seconds", 0) or 0)
            max_wait = min(max(max_wait_value, 0.001), MAX_TIMEOUT_SECONDS) if max_wait_value > 0 else None
            idle = policy.get("idle_timeout_seconds")
            if idle is not None:
                idle = min(max(float(idle), 0.001), MAX_TIMEOUT_SECONDS)
            batch_id = "bat_" + uuid4().hex
            self.store.create_batch(batch_id, route.id, group, event,
                (now + timedelta(seconds=idle)).isoformat() if idle is not None and idle > 0 else None,
                (now + timedelta(seconds=max_wait)).isoformat() if max_wait is not None else datetime.max.replace(tzinfo=timezone.utc).isoformat())
            batch = self.store.get_batch(batch_id)
        idle_deadline = None
        if policy.get("idle_timeout_seconds") is not None and float(policy.get("idle_timeout_seconds") or 0) > 0:
            idle = min(max(float(policy["idle_timeout_seconds"]), 0.001), MAX_TIMEOUT_SECONDS)
            idle_deadline = (now + timedelta(seconds=idle)).isoformat()
        self.store.add_batch_event(batch.id, event, idle_deadline)
        batch = self.store.get_batch(batch.id)
        if immediate:
            self.store.mark_batch_ready(batch.id)
        else:
            max_events = int(min(max(int(policy.get("max_events", 0) or 0), 0), MAX_EVENTS_LIMIT))
            max_chars = int(min(max(int(policy.get("max_chars", 0) or 0), 0), MAX_CHARS_LIMIT))
            if (max_events and batch.event_count >= max_events) or (max_chars and batch.total_chars >= max_chars):
                self.store.mark_batch_ready(batch.id)
        return self.store.get_batch(batch.id)

    def flush_due(self, now: datetime | None = None) -> list[BatchRecord]:
        current = now or datetime.now(timezone.utc)
        ready = []
        routes = {route.id: route for route in self.store.list_routes()}
        for batch in self.store.list_collecting_batches():
            route = routes.get(batch.route_id)
            policy = route.aggregation if route else {}
            active_limits = [
                float(policy.get("max_events", 0) or 0),
                float(policy.get("max_chars", 0) or 0),
                float(policy.get("idle_timeout_seconds", 0) or 0),
                float(policy.get("max_wait_seconds", 0) or 0),
            ]
            # A policy can change while a batch is collecting. Re-evaluate
            # the existing batch so disabling aggregation (or setting
            # max_events=1) cannot leave the first event stranded forever.
            immediate = policy.get("enabled") is False or not policy or not any(value > 0 for value in active_limits)
            max_events = int(policy.get("max_events", 0) or 0)
            max_chars = int(policy.get("max_chars", 0) or 0)
            if immediate or (max_events > 0 and batch.event_count >= max_events) or (max_chars > 0 and batch.total_chars >= max_chars):
                self.store.mark_batch_ready(batch.id)
                ready.append(self.store.get_batch(batch.id))
                continue
            if (_parse(batch.idle_deadline_at) and _parse(batch.idle_deadline_at) <= current) or _parse(batch.max_wait_deadline_at) <= current:
                self.store.mark_batch_ready(batch.id)
                ready.append(self.store.get_batch(batch.id))
        return ready
