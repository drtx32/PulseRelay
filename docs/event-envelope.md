# Event Envelope

The Event Envelope is the core abstraction inside PulseRelay.

Instead of treating all inputs as chat messages, PulseRelay treats everything as events.

Examples:

- A WeChat message
- A GitHub pull request
- A deployment failure
- A cron trigger
- A webhook callback
- A local filesystem change
- A monitoring alert

All of them should eventually become a normalized event envelope.

## Goals

The envelope exists to:

- unify different realtime sources
- preserve original context
- support routing and policy checks
- support AI reasoning
- support local and remote runtimes
- enable replay and audit logs

## Example Shape

```ts
export type EventEnvelope = {
  id: string

  source: {
    type: string
    id?: string
    tenantId?: string
  }

  sender: {
    id?: string
    name?: string
    email?: string
    role?: string
    trustLevel?: string
  }

  event: {
    type: string
    action?: string
    timestamp: string
    dedupeKey?: string
  }

  content: {
    title?: string
    text?: string
    html?: string
    files?: unknown[]
    raw?: unknown
  }

  context: {
    conversationId?: string
    projectId?: string
    repo?: string
    channelId?: string
    threadId?: string
    url?: string
  }

  permissions: {
    allowedTools?: string[]
    allowedAgents?: string[]
    requiresApproval?: boolean
    maxRiskLevel?: string
  }

  routing: {
    priority?: string
    labels?: string[]
    targetAgent?: string
  }
}
```

## Current Migration Path

The current codebase uses a simpler `Message` abstraction in `TriggerEngine`.

The planned migration path is:

```text
Message
  -> EventEnvelope.content

Signals.queue
  -> EventBus

TriggerEngine
  -> Router + TriggerPolicy
```

The existing prototype remains useful because it already provides:

- queue-based processing
- aggregation
- deduplication
- threshold triggering
- idle timeout triggering

The next step is making those mechanisms event-aware instead of message-aware.
