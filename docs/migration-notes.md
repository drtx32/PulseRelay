# Migration Notes

This document tracks the transition from the current message-centric prototype
into the event-native PulseRelay architecture.

## Current State

The current system is centered around:

- `Signals`
- raw dictionaries
- `Message`
- `TriggerEngine`
- source-specific logic

The current queue shape is:

```python
(key, raw_dict)
```

## New Foundation

The new event-native foundation introduces:

- `EventEnvelope`
- `EventBus`
- normalized metadata
- source-independent routing concepts

The new queue shape becomes:

```python
EventRecord(key, EventEnvelope)
```

## Compatibility Strategy

Migration should happen incrementally.

The first compatibility layer is:

```python
EventEnvelope.from_message_dict(raw)
```

This allows old source modules to continue emitting raw dictionaries while the
new routing and runtime layers consume normalized events.

## Planned Refactors

### Phase A

Introduce:

- `core/event.py`
- `core/event_bus.py`

Without changing existing runtime behavior.

### Phase B

Update `Signals.put()` and consumers to internally use `EventEnvelope`.

### Phase C

Refactor `TriggerEngine`:

```text
Message -> EventEnvelope
```

and eventually:

```text
TriggerEngine -> Router + TriggerPolicy
```

### Phase D

Split current gateway responsibilities into:

- source adapters
- router
- runtime dispatcher
- delivery handlers
- approval layer

## Important Principle

The migration should preserve the lightweight local-first development workflow.

PulseRelay should remain easy to run locally while gaining stronger event-native abstractions.
