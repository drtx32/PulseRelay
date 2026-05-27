# Source Adapter

PulseRelay is evolving from a message-driven prototype into an event-native system.

The SourceAdapter abstraction is the new foundation for ingesting realtime events.

## Responsibilities

A source adapter should:

1. connect to an external system
2. receive events
3. normalize payloads into EventEnvelope
4. publish normalized events into EventBus

A source adapter should NOT:

- make routing decisions
- execute agent jobs
- send final notifications
- contain business workflow logic

## Examples

Potential source adapters:

- WeFlowSource
- FeishuSource
- SlackSocketSource
- GitHubWebhookSource
- CronSource
- FileWatcherSource
- LocalRunnerSource
- MCPEventSource

## Lifecycle

```text
created
  -> starting
  -> running
  -> reconnecting
  -> stopped
  -> error
```

This lifecycle becomes important for:

- websocket sources
- reconnect logic
- observability
- health dashboards
- runtime orchestration

## SourceRegistry

The SourceRegistry provides:

- runtime discovery
- health snapshots
- source enumeration
- dynamic registration

This eventually enables plugin-driven sources.

## Relationship to Legacy Module

Old architecture:

```text
Module + Signals
```

New architecture:

```text
SourceAdapter + EventBus
```

The current `base.py` remains as a compatibility bridge while migration continues.

## Future Extensions

The SourceAdapter system is expected to support:

- plugin manifests
- auth metadata
- reconnect policies
- replay cursors
- backpressure handling
- source-scoped permissions
- multi-tenant routing
