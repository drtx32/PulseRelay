# Router Engine

The Router is the decision-making layer inside PulseRelay.

Previous stages established:

```text
SourceAdapter
    -> EventBus
    -> TriggerEngine
    -> DeliveryHandler
```

The Router adds:

```text
classification
policy checks
risk evaluation
capability selection
approval gates
```

## Responsibilities

The router decides:

- what kind of event this is
- how risky it is
- whether it should trigger an agent
- which delivery channel should be used
- whether approval is required

## Current Implementation

The current implementation is intentionally lightweight and deterministic.

It uses:

- keyword classification
- source-type heuristics
- risk scoring
- PolicyEngine evaluation

This keeps the architecture simple while the event model stabilizes.

## Example Flow

```text
GitHub PR Event
    -> classify as "code"
    -> choose claude-code agent
    -> evaluate policy
    -> dispatch to local runtime
    -> deliver result to Feishu
```

## RouteDecision

The router produces a structured routing decision:

```python
RouteDecision(
    target_agent="claude-code",
    target_delivery="feishu",
    risk_level="write",
    requires_approval=False,
)
```

## Why Structured Decisions Matter

Structured decisions enable:

- replay
- audit logs
- approval workflows
- runtime scheduling
- retry handling
- distributed execution

Instead of:

```python
if keyword:
    bark_notify(...)
```

PulseRelay moves toward:

```python
RouteDecision -> Runtime / Delivery / Approval
```

## Future Direction

Future router versions may integrate:

- AI classification
- vector/context retrieval
- capability matching
- dynamic runtime selection
- scheduling and queues
- memory-aware routing
- multi-agent coordination
