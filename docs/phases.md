# PulseRelay Project Phases

This document tracks the architectural evolution of PulseRelay.

PulseRelay started as a lightweight local message aggregation gateway.

The long-term direction is:

> AI-first event relay and local agent orchestration infrastructure.

This file records:

- completed phases
- active phases
- upcoming phases
- architectural dependencies
- migration direction

---

# Current Architectural State

Current architecture:

```text
SourceAdapter
    -> EventBus
    -> TriggerEngine
    -> Router
    -> PolicyEngine
    -> RuntimeDispatcher (planned)
    -> DeliveryHandler
```

Current status:

```text
Event-native foundation established.
Runtime plane not implemented yet.
```

---

# Phase 0 — Prototype Stabilization

Status:

```text
COMPLETED
```

Goal:

Stabilize the original WeFlow/WeChat-trigger prototype before deeper architectural migration.

Original capabilities:

- WeFlow message ingestion
- shared Signals queue
- TriggerEngine aggregation
- Bark push notifications
- FastAPI gateway
- WebSocket support

Main files:

- `gateway.py`
- `base.py`
- `core/trigger_engine.py`

Key outcome:

The project proved that local realtime message aggregation worked reliably enough to evolve into a larger event system.

---

# Phase 1 — Event-Native Foundation

Status:

```text
COMPLETED
```

Related PR:

- PR #2

Goal:

Replace message-centric assumptions with a normalized event abstraction.

Added:

- `core/event.py`
- `core/event_bus.py`
- `EventEnvelope`
- `EventBus`

Key concepts introduced:

```text
source
sender
content
context
permissions
routing metadata
```

Architectural shift:

Old:

```text
message -> trigger -> notify
```

New:

```text
event -> route -> runtime/delivery
```

Key outcome:

PulseRelay became event-native at the data-model level.

---

# Phase 2 — Trigger Layer Migration

Status:

```text
COMPLETED
```

Related PR:

- PR #3

Goal:

Migrate TriggerEngine from message-centric aggregation to event-native aggregation.

Changes:

- TriggerEngine now stores `EventEnvelope`
- dedupe uses `event.dedupe_key`
- compatibility wrappers preserved
- EventBus-compatible queue consumption added

Files:

- `core/trigger_engine.py`
- `docs/trigger-engine-v2.md`

Key outcome:

The trigger layer became source-independent.

Future events no longer need to resemble chat messages.

---

# Phase 3 — SourceAdapter Architecture

Status:

```text
IN REVIEW
```

Related PR:

- PR #4

Goal:

Normalize all ingress systems behind a unified SourceAdapter abstraction.

Added:

- `core/source_adapter.py`
- `SourceAdapter`
- `SourceRegistry`
- source lifecycle states
- source health snapshots

Architectural shift:

Old:

```text
Signals + Module
```

New:

```text
EventBus + SourceAdapter + SourceRegistry
```

Key concepts introduced:

- realtime source lifecycle
- reconnect state
- source capabilities
- registry-based discovery

Key outcome:

PulseRelay ingress became platform-independent.

Future integrations:

- Slack Socket Mode
- Feishu stream
- GitHub webhook
- cron events
- local filesystem watchers
- MCP event sources

all become possible through the same abstraction.

---

# Phase 4 — DeliveryHandler Architecture

Status:

```text
IN REVIEW
```

Related PR:

- PR #5

Goal:

Normalize outbound delivery and action systems.

Added:

- `core/delivery_handler.py`
- `DeliveryHandler`
- `DeliveryRegistry`
- `DeliveryMessage`
- `DeliveryResult`

Key concepts introduced:

- normalized outbound payloads
- delivery state tracking
- structured delivery results
- retry-ready delivery abstraction

Key outcome:

PulseRelay gained normalized egress.

Future outbound systems:

- Bark
- Slack
- Feishu
- Email
- Webhook
- GitHub comments
- MCP tools
- runtime dispatch

can now share one interface.

---

# Phase 5 — Router + Policy Engine

Status:

```text
IN REVIEW
```

Related PR:

- PR #6

Goal:

Introduce orchestration and governance.

Added:

- `core/router.py`
- `core/policy.py`
- `RouteDecision`
- `PolicyDecision`

Current capabilities:

- deterministic classification
- risk scoring
- target selection
- approval requirements
- allowed agent checks
- allowed tool checks

Architectural importance:

This is the first true orchestration layer.

PulseRelay now begins behaving like:

```text
AI-native event orchestration infrastructure
```

instead of a notification relay.

Key outcome:

The system can now reason about:

- what an event means
- how risky it is
- who should handle it
- whether approval is required

---

# Phase 6 — Runtime Dispatcher

Status:

```text
NEXT
```

Goal:

Execute routed work.

Planned additions:

- `core/runtime.py`
- `core/job.py`
- RuntimeDispatcher
- Job abstraction
- execution lifecycle
- runtime selection
- runtime status tracking

Expected architecture:

```text
RouteDecision
    -> Job
    -> RuntimeDispatcher
    -> Runtime
```

Planned runtime types:

- local runner
- CLI coding agents
- MCP servers
- remote HTTP runtimes
- container runtimes

Key challenge:

Separating orchestration from execution.

---

# Phase 7 — Local Runner Protocol

Status:

```text
PLANNED
```

Goal:

Run AI agents safely on local developer machines.

Core idea:

```text
Local Runner
    -> outbound websocket
    -> PulseRelay control plane
```

Planned capabilities:

- job execution
- log streaming
- artifact upload
- heartbeat
- approval requests
- runtime capability advertisement

Target integrations:

- Claude Code
- Codex CLI
- Cursor agent
- MCP servers
- shell tools

Architectural importance:

This phase turns PulseRelay from an event system into an actual distributed agent runtime.

---

# Phase 8 — Plugin System

Status:

```text
PLANNED
```

Goal:

Support dynamic extension loading.

Planned abstractions:

- source plugins
- delivery plugins
- runtime plugins
- router plugins
- policy plugins
- MCP bridges

Expected concepts:

- manifests
- capability advertisement
- sandboxing
- dynamic discovery
- hot reload

Key outcome:

PulseRelay becomes a composable platform instead of a fixed application.

---

# Phase 9 — Replay / Audit / Persistence

Status:

```text
PLANNED
```

Goal:

Make event execution durable and observable.

Planned additions:

- persistent event store
- replay
- dead-letter queues
- audit logs
- execution history
- approval logs
- event timelines

Architectural importance:

This phase is necessary before enterprise or production-grade workflows.

---

# Phase 10 — Control Plane UI

Status:

```text
PLANNED
```

Goal:

Build a developer-facing orchestration dashboard.

Potential capabilities:

- source management
- runtime management
- event timelines
- approval inbox
- replay controls
- plugin management
- routing visualization
- runtime logs
- agent registry

Key outcome:

PulseRelay becomes a usable orchestration platform rather than just a backend framework.

---

# Long-Term Direction

PulseRelay is gradually evolving toward:

```text
AI-native event operating layer
```

rather than:

```text
notification bridge
```

The intended future architecture resembles:

```text
realtime event bus
+ routing engine
+ governance layer
+ local/remote runtimes
+ agent orchestration
+ human approval loops
```

The long-term focus is:

- realtime AI workflows
- local-first agent execution
- event-native orchestration
- human-in-the-loop automation
- lightweight developer control plane
