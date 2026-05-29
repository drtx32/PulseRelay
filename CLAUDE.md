# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PulseRelay is an AI-first event relay for agents, humans, and tools. It receives realtime events from sources (WeFlow/WeChat streams, webhooks, etc.), normalizes them into EventEnvelope objects, routes them through a policy engine, and delivers actionable notifications via Bark, WebSocket, or other handlers.

The project is mid-migration from a message-centric prototype to an event-native architecture (currently in Phase 6 — Runtime Dispatcher).

## Build and Run

```bash
# Configure environment
cp .env.example .env
# Edit .env with your settings

# Run the gateway
uvicorn gateway:app --reload --port 8000
```

## Architecture

```
SourceAdapter (WeFlowSource, etc.)
    ↓
EventBus (queue.SimpleQueue)
    ↓
TriggerEngine (aggregation + deduplication)
    ↓
Router (classifies events, assigns risk level)
    ↓
PolicyEngine (approval gates, tool/agent allowlists)
    ↓
DeliveryHandler (Bark, WebSocket, etc.)
```

### Core Abstractions

| File | Purpose |
|------|---------|
| `core/event.py` | EventEnvelope dataclass — the normalized event shape |
| `core/event_bus.py` | In-memory event queue bridging sources and triggers |
| `core/source_adapter.py` | SourceAdapter ABC + SourceRegistry |
| `core/trigger_engine.py` | Aggregates events, fires when thresholds reached |
| `core/router.py` | Classifies events, selects target agent/delivery |
| `core/policy.py` | PolicyEngine — risk checks, approval gates |
| `core/delivery_handler.py` | DeliveryHandler ABC + DeliveryRegistry |
| `base.py` | Legacy Signals/Module bridge (being phased out) |

### Entry Point

`gateway.py` — FastAPI app with WebSocket support. Initializes sources, trigger engine, and delivery handlers on startup. Runs a background thread main loop for signal consumption and trigger checking.

## Key Patterns

### Adding a New Source
1. Inherit `SourceAdapter` from `core/source_adapter.py`
2. Define `manifest` (SourceManifest) and `run()` method
3. Register in `gateway.py` startup via `Signals.register_source()`

### Adding a New Delivery
1. Inherit `DeliveryHandler` from `core/delivery_handler.py`
2. Implement `deliver(message, event)` method
3. Register in delivery registry

### Event Flow
Events flow through: `SourceAdapter.emit()` → `EventBus.put()` → `TriggerEngine.consume_signals()` → `TriggerEngine.process_event()` → `TriggerEngine.check_trigger()` → downstream delivery/routing

### Migration Status
- Phases 1–5 complete (EventEnvelope, Trigger migration, SourceAdapter, DeliveryHandler, Router+Policy)
- Phase 6 (RuntimeDispatcher) is next — not yet implemented
- `base.py` and `Signals` class are legacy compatibility wrappers; prefer direct EventBus usage

## Configuration

Runtime config is loaded from `data/config.yaml` via `ConfigLoader` in `core/config.py`. Environment variables in `.env` provide local overrides (see `.env.example`).

## Dependencies

```
fastapi>=0.100.0, uvicorn>=0.23.0, websockets>=11.0.0,
python-dotenv>=1.0.0, requests>=2.28.0, jinja2>=3.1.0
```
