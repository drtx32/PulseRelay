# Source Adapter

Source adapters are the ingress layer of PulseRelay.

Their job is to convert platform-specific events into normalized EventEnvelope objects.

## Responsibilities

A source adapter is responsible for:

- connection lifecycle
- authentication/signature verification
- websocket/session management
- reconnect logic
- payload normalization
- event publishing
- source health reporting

## Architectural Position

```text
External Platform
    |
    v
SourceAdapter
    |
    v
EventEnvelope
    |
    v
EventBus
```

## Why This Matters

Previously, PulseRelay assumed that all inputs were chat messages.

This abstraction removes that assumption.

A source can now be:

- WeChat stream
- Feishu bot
- Slack Socket Mode
- GitHub webhook
- cron scheduler
- filesystem watcher
- MCP event source
- browser extension
- monitoring alert

without changing downstream routing logic.

## Source Manifest

Each adapter declares a manifest.

Example:

```python
SourceManifest(
    id="github-webhook",
    type="github",
    name="GitHub Webhook",
    supports_webhook=True,
    supports_replay=True,
    capabilities=["issues", "pull-requests"],
)
```

The manifest helps the future control plane understand:

- what a source supports
- how it connects
- what events it emits
- what capabilities it exposes

## Health Model

Every source should expose health information:

```python
SourceHealth(
    connected=True,
    last_event_at="...",
    last_error="...",
)
```

This becomes important for:

- reconnect supervision
- observability
- monitoring
- debugging
- local runner management

## Migration Strategy

The current codebase still contains message-centric source logic.

`LegacyMessageSourceAdapter` acts as a compatibility bridge while existing sources migrate toward native EventEnvelope production.
