# Agent Runtime

PulseRelay is designed around the idea that AI agents are event-driven workers.

A runtime is responsible for executing work triggered by routed events.

## Runtime Types

Planned runtime types:

- Local runner
- CLI coding agents
- MCP servers
- Remote HTTP agents
- Containerized workers
- Serverless workers

## Local Runner

The local runner is expected to become one of the most important components.

It should behave similarly to a lightweight GitHub Actions Runner for AI agents.

### Responsibilities

- establish an outbound WebSocket connection
- receive jobs from the control plane
- execute local tools and agents
- access local repositories and files
- stream logs/results back
- enforce local policies

## Why Outbound WebSocket

Most local machines are not publicly reachable.

Instead of exposing a public webhook endpoint on the local machine, the runner maintains an outbound connection:

```text
Local Runner -> WebSocket -> PulseRelay Control Plane
```

The control plane can then dispatch jobs over the existing connection.

## Example Flow

```text
GitHub PR opened
    |
    v
PulseRelay receives webhook
    |
    v
Router decides to invoke coding agent
    |
    v
Job dispatched to local runner
    |
    v
Claude Code / Codex analyzes repository
    |
    v
Summary returned to PulseRelay
    |
    v
Result delivered to Slack / Feishu / GitHub
```

## Future Runtime Protocol

The runtime protocol will likely include:

- heartbeat
- capability advertisement
- job assignment
- streaming logs
- approval requests
- artifact upload
- cancellation
- retry

The protocol design is intentionally postponed until the event and routing layers become more stable.
