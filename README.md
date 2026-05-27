# PulseRelay

PulseRelay is an AI-first event relay for agents, humans, and tools.

It starts as a lightweight local gateway that can listen to realtime sources such as WeFlow/WeChat streams, aggregate noisy messages, and deliver actionable notifications through channels such as Bark or WebSocket. The long-term goal is to evolve it into an event-native agent orchestration layer: every inbound signal is normalized into a structured event, routed by rules and AI, and then handled by local or remote agent runtimes.

## Why PulseRelay

Traditional notification tools usually treat input as a message:

```text
message in -> notification out
```

PulseRelay treats input as an event:

```text
source + sender + event + content + context + policy -> route -> agent/action/delivery
```

This makes it suitable for AI-native workflows where a message is not just something to display, but something that can trigger summarization, prioritization, approval, tool calls, or local agent work.

## Current Status

The current codebase is an early prototype. It already includes:

- A shared `Signals` queue and `Module` base class for source modules.
- A `TriggerEngine` that aggregates messages and triggers when content length, message count, or idle timeout thresholds are reached.
- A FastAPI gateway with WebSocket support.
- A WeFlow/WeChat-oriented source path.
- Bark notification support for iOS push delivery.

The current prototype is still message-centric. The planned architecture is event-centric.

## Target Architecture

```text
External Sources
  WeFlow / Feishu / Slack / GitHub / Webhook / Cron / Local events
        |
        v
Source Adapters
  verify -> normalize -> dedupe
        |
        v
Event Bus / Event Inbox
  persist -> queue -> replay -> rate limit
        |
        v
Router / Policy Engine
  rules -> AI classification -> capability matching -> approval gate
        |
        v
Agent Runtime / Action Layer
  local runner -> CLI agents -> MCP tools -> webhooks -> APIs
        |
        v
Delivery Layer
  Bark / Feishu / Slack / Email / WebSocket / GitHub comments
```

## Core Concepts

### Event Envelope

A normalized event object that carries source, sender, content, context, permissions, routing metadata, and raw platform payload.

See [`docs/event-envelope.md`](docs/event-envelope.md).

### Source Adapter

A source adapter connects to an external source and converts platform-specific events into a PulseRelay event envelope. A source can be a webhook receiver, a WebSocket/SSE listener, a cron trigger, or a local file/process watcher.

### Event Bus

The event bus receives normalized events, deduplicates them, and makes them available to routers, triggers, agents, and delivery handlers.

### Router / Policy Engine

The router decides what should happen next. It can combine deterministic rules, natural-language rules, LLM classification, sender trust, capability matching, and approval policies.

### Agent Runtime

A runtime executes work on behalf of a routed event. It can be a cloud worker, a local runner, a CLI coding agent, an MCP server, or a webhook-based external agent.

See [`docs/agent-runtime.md`](docs/agent-runtime.md).

### Delivery Handler

A delivery handler sends the result to a human, system, or another agent. Examples include Bark push, Feishu messages, Slack messages, GitHub comments, email, or outbound webhooks.

## Short-Term Roadmap

See [`docs/roadmap.md`](docs/roadmap.md).

High-level phases:

1. Document the event-native architecture and stabilize the current prototype.
2. Introduce `EventEnvelope` while keeping compatibility with the existing message trigger flow.
3. Split source adapters, triggers, routers, runtimes, and deliveries into explicit extension points.
4. Add a local runner protocol for running local AI agents safely through an outbound WebSocket connection.
5. Add replay, audit log, approval gates, and plugin manifests.

## Development

Create a `.env` file from `.env.example`, then run the gateway:

```bash
uvicorn gateway:app --reload --port 8000
```

This command reflects the current prototype entrypoint and may change as the project moves toward the event-native architecture.

## Project Direction

PulseRelay is not just a push-notification bridge. The intended direction is:

> AI-first event relay and local agent runtime gateway.

It should eventually feel like a small, developer-friendly control plane for routing realtime events to AI agents, humans, and tools.