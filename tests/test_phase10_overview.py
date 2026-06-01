"""
Phase 10 overview endpoint tests.
"""

from __future__ import annotations

import pytest

from base import Signals
import gateway
from core.event import EventContent, EventEnvelope, EventMeta, EventSource
from core.persistence import SQLitePhase9Store


def _event(event_id: str) -> EventEnvelope:
    return EventEnvelope(
        id=event_id,
        source=EventSource(type="test_source", id="c1", name="Test"),
        event=EventMeta(type="message.created", dedupe_key=f"d:{event_id}"),
        content=EventContent(text="hello"),
    )


@pytest.mark.asyncio
async def test_phase10_overview_with_phase9_enabled(tmp_path):
    store = SQLitePhase9Store(db_path=tmp_path / "phase9.db")
    signals = Signals()
    signals.event_bus.attach_store(store)
    signals.event_bus.put("test_source", _event("evt_1"))
    signals.event_bus.put("test_source", _event("evt_2"))

    original_store = gateway._phase9_store
    original_signals = gateway._signals
    original_sources = gateway._sources

    class _Source:
        def __init__(self, source_id: str, state: str):
            self.source_id = source_id
            self.health = type("Health", (), {"state": state, "last_event_at": "", "last_error": "", "reconnect_count": 0})()

    try:
        gateway._phase9_store = store
        gateway._signals = signals
        gateway._sources = {"a": object(), "b": object()}
        signals.sources._sources = {  # noqa: SLF001 (test-only access)
            "a": _Source("a", "running"),
            "b": _Source("b", "stopped"),
        }

        data = await gateway.phase10_overview()
        assert data["phase9_enabled"] is True
        assert data["sources_registered"] == 2
        assert data["sources_running"] == 1
        assert data["phase9"]["events_total"] == 2
    finally:
        gateway._phase9_store = original_store
        gateway._signals = original_signals
        gateway._sources = original_sources
        store.close()
