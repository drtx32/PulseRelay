# Policy Engine

The Policy Engine is the safety and governance layer of PulseRelay.

The Router decides what should happen.

The Policy Engine decides whether it is allowed.

## Responsibilities

The policy layer evaluates:

- risk levels
- allowed agents
- allowed tools
- approval requirements
- execution permissions

## Why This Matters

AI-native systems eventually become action systems.

Once PulseRelay can:

- deploy code
- merge pull requests
- modify files
- send messages
- invoke MCP tools
- access local runtimes

it needs explicit governance.

## Example

```text
Event:
    GitHub deployment request

Router:
    deployment-reviewer agent
    risk=deploy

Policy Engine:
    deploy exceeds allowed risk
    -> requires approval
```

## Current Implementation

The current implementation is deterministic and lightweight.

It evaluates:

- max risk level
- allowed tools
- allowed agents
- approval requirements

## Future Direction

Future policy systems may include:

- RBAC
- tenant isolation
- audit signatures
- approval workflows
- AI-assisted safety checks
- runtime sandboxing
- capability-scoped tokens
- dynamic trust scoring

## Important Principle

The policy layer should remain explainable.

Even if AI-assisted reasoning is introduced later, PulseRelay should preserve:

- deterministic enforcement
- structured decisions
- replayability
- auditable execution paths
