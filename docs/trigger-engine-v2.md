# Trigger Engine v2

The original TriggerEngine was message-centric.

It aggregated raw dictionaries shaped like chat messages:

```python
(chat, sender, content)
```

The new TriggerEngine is event-native.

## Architectural Shift

Old flow:

```text
raw dict -> Message -> TriggerEngine
```

New flow:

```text
raw dict
    -> EventEnvelope
    -> TriggerEngine
    -> TriggerResult(events=[...])
```

The compatibility layer still exposes:

- `process_raw()`
- `messages`

so existing handlers and templates continue to work.

## Important Changes

### Internal State

Old:

```python
self.messages: list[Message]
```

New:

```python
self.events: list[EventEnvelope]
```

### Deduplication

Old:

```python
(chat, local_id)
```

New:

```python
event.dedupe_key
```

This makes deduplication source-independent.

## Why This Matters

This change allows TriggerEngine to eventually become a generalized event router.

Future event types may include:

- GitHub pull requests
- deployment failures
- webhook callbacks
- local file events
- cron triggers
- approval requests
- agent job completions

without requiring a message-specific schema.

## Next Evolution

The next architectural step is:

```text
TriggerEngine
    -> TriggerPolicy + Router
```

where:

- TriggerPolicy decides WHEN something should happen
- Router decides WHAT should happen next

This separation is important for AI-native workflows.
