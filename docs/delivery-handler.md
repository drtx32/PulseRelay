# Delivery Handler

Delivery handlers are the outbound side of PulseRelay.

If SourceAdapter is responsible for ingesting realtime events, DeliveryHandler is responsible for delivering processed events to humans, systems, or downstream agents.

## Responsibilities

A delivery handler should:

- receive normalized outbound messages
- format payloads for one destination type
- send notifications or actions
- return structured delivery results

A delivery handler should NOT:

- decide routing
- classify events
- execute agent jobs
- contain workflow logic

## Architecture Position

```text
SourceAdapter
    -> EventBus
    -> Trigger / Router
    -> DeliveryHandler
```

DeliveryHandler is intentionally downstream from routing.

## Examples

Potential delivery handlers:

- BarkDelivery
- FeishuDelivery
- SlackDelivery
- EmailDelivery
- WebhookDelivery
- GitHubCommentDelivery
- WebSocketDelivery
- MCPToolDelivery

## DeliveryMessage

Delivery handlers consume a normalized outbound message:

```python
DeliveryMessage(
    title=...,
    text=...,
    html=...,
    files=[...],
    metadata={...},
)
```

This avoids leaking EventEnvelope internals into destination-specific formatting logic.

## DeliveryResult

All deliveries return a normalized result:

```python
DeliveryResult(
    status="success" | "failed" | "skipped",
    external_id="...",
    error="...",
)
```

This becomes important for:

- retries
- audit logs
- replay
- observability
- approval workflows

## DeliveryRegistry

The registry enables:

- runtime discovery
- capability matching
- dynamic registration
- plugin-based delivery systems

## Future Direction

Delivery handlers are expected to evolve beyond notifications.

Future handlers may:

- invoke MCP tools
- call external APIs
- create GitHub issues
- open pull requests
- send approval requests
- dispatch jobs to local runners

At that point, the distinction between "notification" and "action" becomes much smaller.
