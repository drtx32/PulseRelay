# Roadmap

## Phase 0 - Prototype Stabilization

Current state.

Goals:

- stabilize the current WeFlow + TriggerEngine flow
- improve logging and observability
- document the architecture
- clarify naming and module boundaries

## Phase 1 - Event-Native Foundation

Goals:

- introduce EventEnvelope
- replace raw dict passing with typed event objects
- rename Signals.queue into EventBus concepts
- make trigger logic event-aware
- add persistent event storage

## Phase 2 - Source and Delivery Extensions

Goals:

- formalize SourceAdapter interface
- formalize DeliveryHandler interface
- add outbound webhook delivery
- add Feishu and Slack delivery support
- support replay and dead-letter queues

## Phase 3 - Router and Policies

Goals:

- add routing rules
- add natural-language routing policies
- add sender trust and permission checks
- add approval gates
- support AI-assisted event classification

## Phase 4 - Agent Runtime

Goals:

- add local runner
- add outbound WebSocket runtime protocol
- support CLI coding agents
- support MCP servers
- support job lifecycle management

## Phase 5 - Plugin Ecosystem

Goals:

- add extension manifests
- dynamic source registration
- dynamic runtime registration
- capability advertisement
- sandboxed plugins

## Long-Term Direction

PulseRelay should eventually become:

> An AI-first event relay and orchestration layer for realtime agents.
