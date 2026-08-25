"""Application service composing persistence, routing, aggregation and delivery."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from core.aggregation import Aggregator
from core.event import EventEnvelope, ensure_event
from core.persistence import SQLitePhase9Store
from core.routing import matching_routes
from core.webhook_delivery import WebhookDelivery, WebhookResult, stable_idempotency_key
from core.event_bus import EventBus


class DurableRelay:
    def __init__(self, store: SQLitePhase9Store, worker_id: str = "relay", event_bus: EventBus | None = None):
        self.store, self.worker_id = store, worker_id
        self.event_bus = event_bus
        self.aggregator = Aggregator(store)

    def ingest(self, value: EventEnvelope | dict, source_type: str = "webhook") -> tuple[EventEnvelope, bool]:
        event = ensure_event(value, source_type=source_type)
        before = self.store.get_event_id_by_dedupe(event.dedupe_key)
        self.store.record_event(source_type, event)
        duplicate = before is not None
        if duplicate:
            self.store.record_audit("event.duplicate", "event", event.id, metadata={"dedupe_key": event.dedupe_key})
            return ensure_event(self.store.get_event_payload(before)), True
        self.store.record_audit("event.received", "event", event.id)
        if self.event_bus:
            self.event_bus.publish(event.to_dict() | {"status": "received", "received_at": self.store._now()})
        # Close idle batches before admitting the new event. This prevents a
        # late third message from being appended to a batch whose quiet
        # timeout already expired while the periodic worker was between ticks.
        self.flush()
        for route in matching_routes(event, self.store.list_routes(enabled_only=True)):
            batch = self.aggregator.add(event, route)
            if batch.status == "ready": self._queue_batch(batch.id, route.id)
        return event, False

    def _queue_batch(self, batch_id: str, route_id: str) -> int:
        batch = self.store.get_batch(batch_id)
        destinations = self.store.route_destinations(route_id)
        count = 0
        for destination_id in destinations:
            key = stable_idempotency_key(batch_id, destination_id)
            if self.store.create_delivery("del_" + uuid4().hex, destination_id, key, batch_id=batch_id): count += 1
        return count

    def flush(self) -> int:
        count = 0
        for batch in self.aggregator.flush_due():
            route = next((r for r in self.store.list_routes() if r.id == batch.route_id), None)
            if route: count += self._queue_batch(batch.id, route.id)
        return count

    async def process_one(self) -> WebhookResult | None:
        delivery = self.store.claim_delivery(self.worker_id)
        if delivery is None: return None
        destination = self.store.get_destination(delivery.destination_id)
        if destination is None:
            result = WebhookResult("failed", error="destination not found")
        else:
            subject_id = delivery.batch_id or delivery.event_id or delivery.id
            aggregation_policy = None
            if delivery.batch_id:
                events = self.store.batch_events(delivery.batch_id)
                route = next((item for item in self.store.list_routes() if item.id == self.store.get_batch(delivery.batch_id).route_id), None)
                aggregation_policy = route.aggregation if route else None
                subject = {"batch_id": delivery.batch_id, "events": [event.to_dict() for event in events],
                           "event_count": len(events),
                           "content": {"text": "\n".join(event.content.text for event in events)}}
            else:
                payload = self.store.get_event_payload(delivery.event_id)
                subject = ensure_event(payload).to_dict()
            result = await WebhookDelivery(destination, allow_private=destination.config.get("allow_private", False)).send(
                subject, subject_id, delivery.id, aggregation=aggregation_policy)
        if result.status == "success":
            self.store.update_delivery(delivery.id, "success", http_status=result.http_status, request_excerpt=result.request_excerpt, response_excerpt=result.response_excerpt, external_id=result.external_id)
            if result.emitted_event:
                emitted = ensure_event(result.emitted_event)
                emitted.context.extra["causation_delivery_id"] = delivery.id
                self.ingest(emitted, source_type="webhook_result")
        else:
            max_attempts = int((destination.config if destination else {}).get("max_attempts", 10))
            if delivery.attempts >= max_attempts:
                self.store.update_delivery(delivery.id, "dead_letter", http_status=result.http_status, error=result.error, request_excerpt=result.request_excerpt, response_excerpt=result.response_excerpt)
                if delivery.event_id: self.store.record_dead_letter(self.store.get_event_payload(delivery.event_id), "delivery", result.error)
            else:
                delay = result.retry_after or min(3600, 10 * (3 ** max(0, delivery.attempts - 1)))
                next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                self.store.update_delivery(delivery.id, "retrying", http_status=result.http_status, error=result.error, request_excerpt=result.request_excerpt, response_excerpt=result.response_excerpt, next_attempt_at=next_at)
        return result
