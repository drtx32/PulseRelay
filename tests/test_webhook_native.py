from __future__ import annotations

from pathlib import Path

from core.aggregation import Aggregator
from core.event import EventContent, EventEnvelope, EventMeta, EventSource
from core.persistence import SQLitePhase9Store
from core.relay import DurableRelay
from connectors.gbrain.connector import is_verified_completed, normalize_run, path_is_watched


def event(event_id: str, text: str = "x", dedupe: str | None = None):
    return EventEnvelope(id=event_id, source=EventSource(type="wechat", id="group"),
                         event=EventMeta(type="message.created", dedupe_key=dedupe or event_id),
                         content=EventContent(text=text))


def test_durable_fanout_and_route_scoped_batch(tmp_path: Path):
    store = SQLitePhase9Store(tmp_path / "relay.db")
    store.upsert_destination("one", "One", "https://example.test/one")
    store.upsert_destination("two", "Two", "https://example.test/two")
    store.upsert_route("r", "Research", {"source.type": "wechat"},
                       {"enabled": True, "group_by": ["context.conversation_id"], "max_events": 2,
                        "max_wait_seconds": 300}, destinations=["one", "two"])
    relay = DurableRelay(store)
    relay.ingest(event("e1", "first"))
    assert store.list_deliveries() == []
    relay.ingest(event("e2", "second"))
    deliveries = store.list_deliveries()
    assert len(deliveries) == 2
    assert {item.destination_id for item in deliveries} == {"one", "two"}


def test_disabled_aggregation_delivers_each_event_immediately(tmp_path: Path):
    store = SQLitePhase9Store(tmp_path / "disabled-aggregation.db")
    store.upsert_destination("one", "One", "https://example.test/one")
    store.upsert_route("r", "Immediate", {"source.type": "wechat"},
                       {"enabled": False, "max_events": 0, "max_chars": 0,
                        "idle_timeout_seconds": 0, "max_wait_seconds": 0},
                       destinations=["one"])
    relay = DurableRelay(store)

    relay.ingest(event("e1", "first"))
    relay.ingest(event("e2", "second"))

    deliveries = store.list_deliveries()
    assert len(deliveries) == 2
    assert all(item.batch_id for item in deliveries)


def test_policy_change_releases_existing_batch(tmp_path: Path):
    store = SQLitePhase9Store(tmp_path / "policy-change.db")
    store.upsert_destination("one", "One", "https://example.test/one")
    store.upsert_route("r", "Batched", {"source.type": "wechat"},
                       {"enabled": True, "max_events": 3}, destinations=["one"])
    relay = DurableRelay(store)
    relay.ingest(event("e1"))
    assert store.list_deliveries() == []
    store.upsert_route("r", "Immediate", {"source.type": "wechat"},
                       {"enabled": False}, destinations=["one"])
    relay.flush()
    assert len(store.list_deliveries()) == 1


def test_gbrain_connector_normalizes_completed_output():
    payload = normalize_run({"id": "run-1", "status": "completed", "verification": "passed",
                             "output_slug": "notes/1", "completed_at": "2026-08-19T08:20:00+00:00"}, "digest")
    assert payload["event"]["type"] == "gbrain.output.ready"
    assert payload["content"]["text"] == "digest"
    assert payload["event"]["dedupe_key"].startswith("gbrain:run-1:")


def test_gbrain_connector_accepts_current_verified_output_schema():
    assert is_verified_completed({"status": "completed", "durable_output_verified": True})
    assert not is_verified_completed({"status": "completed", "durable_output_verified": False})
    assert is_verified_completed({"status": "completed", "verification": "passed"})


def test_gbrain_connector_path_filter_is_allow_then_deny(monkeypatch):
    monkeypatch.setattr("connectors.gbrain.connector.WATCH_PATHS", ["notes/**"])
    monkeypatch.setattr("connectors.gbrain.connector.IGNORE_PATHS", ["notes/ashare-observation/**/*plan*"])
    assert path_is_watched("notes/trendradar-digests/today")
    assert path_is_watched("notes/ashare-observation/today")
    assert not path_is_watched("notes/ashare-observation/today-plan")
    assert not path_is_watched("other/today")


def test_event_dedupe_is_idempotent(tmp_path: Path):
    store = SQLitePhase9Store(tmp_path / "dedupe.db")
    relay = DurableRelay(store)
    first, first_duplicate = relay.ingest(event("e1", dedupe="same"))
    second, second_duplicate = relay.ingest(event("e2", dedupe="same"))
    assert not first_duplicate
    assert second_duplicate and first.id == second.id
    assert store.stats()["events_total"] == 1
