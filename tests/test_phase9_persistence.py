"""
Phase 9 persistence and replay tests.
"""

from __future__ import annotations

from core.event import EventContent, EventEnvelope, EventMeta, EventSource
from core.event_bus import EventBus
from core.persistence import SQLitePhase9Store


def _build_event(event_id: str, text: str, event_type: str = "message.created") -> EventEnvelope:
    return EventEnvelope(
        id=event_id,
        source=EventSource(type="test_source", id="chat_1", name="Test Source"),
        event=EventMeta(type=event_type, dedupe_key=f"dedupe:{event_id}"),
        content=EventContent(text=text, raw={"text": text}),
    )


def test_phase9_store_records_and_lists_events(tmp_path):
    db_path = tmp_path / "phase9.db"
    store = SQLitePhase9Store(db_path=db_path)
    try:
        event = _build_event("evt_1", "hello phase9")
        store.record_event(bus_key="test", event=event)

        events = store.list_events(limit=10)
        assert len(events) == 1
        assert events[0].event_id == "evt_1"
        assert events[0].source_type == "test_source"
        assert events[0].event_type == "message.created"
        assert events[0].payload["content"]["text"] == "hello phase9"
    finally:
        store.close()


def test_phase9_store_dead_letter_and_audit(tmp_path):
    db_path = tmp_path / "phase9.db"
    store = SQLitePhase9Store(db_path=db_path)
    try:
        event = _build_event("evt_2", "broken payload")
        store.record_dead_letter(event=event, stage="trigger.process", error="boom")
        store.record_audit(
            action="event.processed",
            status="failed",
            entity_type="event",
            entity_id=event.id,
            message="boom",
            metadata={"stage": "trigger.process"},
        )

        dead_letters = store.list_dead_letters(limit=10)
        assert len(dead_letters) == 1
        assert dead_letters[0].event_id == "evt_2"
        assert dead_letters[0].stage == "trigger.process"

        logs = store.list_audit_logs(limit=10)
        assert len(logs) == 1
        assert logs[0].action == "event.processed"
        assert logs[0].status == "failed"
    finally:
        store.close()


def test_phase9_event_bus_replay(tmp_path):
    db_path = tmp_path / "phase9.db"
    store = SQLitePhase9Store(db_path=db_path)
    try:
        bus = EventBus(store=store)
        bus.put("test", _build_event("evt_3", "first"))
        bus.put("test", _build_event("evt_4", "second"))

        replay_count = bus.replay(limit=2, source_type="test_source")
        assert replay_count == 2

        first = bus.get(timeout=0.1)
        second = bus.get(timeout=0.1)
        replay_first = bus.get(timeout=0.1)
        replay_second = bus.get(timeout=0.1)

        assert first.event.id == "evt_3"
        assert second.event.id == "evt_4"
        assert replay_first.event.id == "evt_3"
        assert replay_second.event.id == "evt_4"
    finally:
        store.close()
