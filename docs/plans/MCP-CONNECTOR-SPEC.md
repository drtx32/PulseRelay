# PulseRelay MCP Connector Orchestration — P0 Implementation Spec

- **Status:** P0 / implementation priority
- **Audience:** Oracle Codex / maintainers
- **Repository:** `drtx32/PulseRelay`
- **Supersedes for priority:** the assumption in `docs/plans/SPEC.md` that MCP transports are out of scope and outbound must be webhook-only.

## 1. Product boundary

PulseRelay is **not an Agent Runtime**. Its core job is connection and event orchestration.

The P0 product model is:

```text
Connector
  -> Capability / Event normalization
  -> Route / Chain
  -> Connector
```

A connector can be ingress, egress, bidirectional, pull/poll, or tool-call based. MCP and Webhook are first-class connector transports, not special cases hidden in business code.

The immediate goal is to make PulseRelay able to:

1. create and manage connectors;
2. connect to MCP servers and expose their tools/capabilities;
3. create inbound webhooks;
4. create outbound webhooks;
5. chain connectors through routes/flows;
6. inspect health, capabilities, schemas, execution history, errors, and secrets references;
7. keep protocol transport separate from business logic.

Do **not** prioritize message accumulation, AI-agent execution, long-lived agent sessions, or new destination-specific integrations ahead of this P0.

## 2. Reference pattern from GBrain

Follow the same architectural principle used by GBrain's dual-transport MCP design:

> business logic and tool definitions are shared; stdio and Streamable HTTP are transport adapters only.

For PulseRelay, generalize this one level further:

```text
Connector Definition
        |
        v
Connector Driver
  - MCP
  - Webhook Inbound
  - Webhook Outbound
  - HTTP/API
  - Polling
  - Stdio process (later)
  - WebSocket (later)
        |
        v
Normalized Capability / Event / Invocation
        |
        v
Route / Chain Engine
```

The routing engine must not know whether the upstream/downstream connector is MCP, webhook, or another transport.

## 3. Connector model

Create a persistent `Connector` entity.

Suggested shape:

```json
{
  "id": "conn_...",
  "name": "GBrain",
  "kind": "mcp",
  "direction": "bidirectional",
  "enabled": true,
  "transport": {
    "type": "streamable_http",
    "url": "https://.../mcp"
  },
  "auth": {
    "type": "bearer",
    "secret_ref": "secret://gbrain-token"
  },
  "capabilities": {
    "tools": true,
    "resources": true,
    "prompts": false
  },
  "metadata": {},
  "created_at": "...",
  "updated_at": "..."
}
```

Required connector kinds for P0:

- `mcp`
- `webhook_inbound`
- `webhook_outbound`

Design the driver interface so HTTP/API, polling, stdio and WebSocket can be added later without changing route semantics.

## 4. Connector Registry

Implement a `ConnectorRegistry` as the canonical runtime + persistence facade.

Minimum operations:

```text
create_connector
get_connector
list_connectors
update_connector
delete_connector / disable_connector
connect / disconnect
health_check
refresh_capabilities
list_capabilities
invoke_capability
```

The registry owns lifecycle and delegates protocol behavior to drivers.

Suggested interface:

```python
class ConnectorDriver(Protocol):
    async def connect(self, config): ...
    async def disconnect(self): ...
    async def health(self) -> HealthStatus: ...
    async def discover(self) -> CapabilitySnapshot: ...
    async def invoke(self, capability, payload) -> InvocationResult: ...
```

Do not put MCP-specific fields directly into generic route records.

## 5. MCP Connector — highest priority

### 5.1 Client support

PulseRelay must act as an MCP client and connect to existing MCP servers.

P0 transports:

1. **Streamable HTTP** — required.
2. **stdio** — implement if straightforward, otherwise P0.1 immediately after HTTP.

Do not create two copies of MCP business logic for two transports.

### 5.2 Discovery

After connect, discover and persist/cache:

- tools and JSON schemas;
- resources and templates, if exposed;
- prompts, if exposed;
- server identity/version;
- transport/auth metadata excluding secrets;
- discovery timestamp and content hash/version.

Expose API endpoints such as:

```http
POST   /v1/connectors
GET    /v1/connectors
GET    /v1/connectors/{id}
POST   /v1/connectors/{id}/connect
POST   /v1/connectors/{id}/refresh
GET    /v1/connectors/{id}/capabilities
POST   /v1/connectors/{id}/invoke
```

### 5.3 Invocation

A route/flow must be able to invoke an MCP tool without embedding MCP protocol logic in the flow engine.

Normalized action:

```json
{
  "connector_id": "conn_gbrain",
  "capability_type": "tool",
  "capability_name": "get_page",
  "arguments": {
    "slug": "..."
  }
}
```

The MCP driver converts that to the wire protocol.

### 5.4 MCP as a source

MCP is not only an action target. A connector may also be used to poll/read a remote system and emit normalized PulseRelay events.

Do not hardcode a GBrain-specific connector into core. GBrain should be the first integration test of the generic MCP Connector.

## 6. Webhook Inbound Connector

Creating a `webhook_inbound` connector should automatically create an ingress endpoint.

Example:

```http
POST /v1/hooks/{connector_id}/{secret}
```

Requirements:

- generated secret stored hashed or in secret storage;
- request size/content-type limits;
- optional HMAC verification;
- configurable normalization mapping/template;
- stable event id and dedupe key;
- persistence before downstream side effects;
- `2xx` only after accepted event persistence;
- regenerate/rotate secret;
- disable connector immediately blocks ingress.

A user should be able to create the connector without editing source code.

## 7. Webhook Outbound Connector

A `webhook_outbound` connector represents a reusable HTTP destination.

Configuration:

```json
{
  "url": "https://example.com/hook",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer ${secret:destination-token}"
  },
  "timeout_seconds": 30,
  "success_statuses": [200, 201, 202, 204],
  "retry": {
    "max_attempts": 8,
    "strategy": "exponential"
  },
  "payload_template": "..."
}
```

Requirements:

- durable delivery record;
- retry/backoff;
- idempotency header/key;
- redacted logs;
- replay;
- independent fan-out state.

## 8. Route / Chain model

The key user-facing feature is the ability to **chain connectors**.

Examples:

```text
Webhook Inbound
  -> MCP:GBrain.get_page
  -> Webhook Outbound:Multica
```

```text
MCP:GBrain query/poll
  -> filter/transform
  -> Webhook Outbound:Wiki Publisher
  -> on success emit artifact.published
  -> Webhook Outbound:Multica
```

```text
Webhook Inbound:GitHub
  -> filter pull_request.opened
  -> MCP:review-tools.review_pr
  -> Webhook Outbound:notification
```

Represent a flow as nodes + edges, not destination-specific code.

Suggested minimal model:

```json
{
  "id": "flow_...",
  "name": "gbrain-to-multica",
  "enabled": true,
  "nodes": [
    {"id": "n1", "type": "connector_trigger", "connector_id": "conn_in"},
    {"id": "n2", "type": "connector_action", "connector_id": "conn_gbrain", "capability": "get_page"},
    {"id": "n3", "type": "transform", "template": "..."},
    {"id": "n4", "type": "connector_action", "connector_id": "conn_multica"}
  ],
  "edges": [
    ["n1", "n2"],
    ["n2", "n3"],
    ["n3", "n4"]
  ]
}
```

P0 does not need a complex BPMN engine. A DAG with deterministic sequential execution and simple fan-out is enough.

Required node types:

- connector trigger
- connector action
- filter
- transform
- emit event

Later:

- delay
- approval
- branch/switch
- aggregation

## 9. API and UI acceptance

A user must be able to perform the following without editing code:

1. Create an MCP connector by URL + auth reference.
2. Connect and see discovered MCP tools.
3. Test-call one tool and inspect structured result/error.
4. Create an inbound webhook and copy its generated URL.
5. Send a sample payload and see a persisted event.
6. Create an outbound webhook and send a test delivery.
7. Create a chain from inbound webhook -> MCP tool -> outbound webhook.
8. Execute it end-to-end and inspect every node's input/output/status.
9. Disable any connector and observe deterministic failure/skip behavior.
10. Rotate secrets without rewriting flows.

## 10. Persistence

Extend the current SQLite storage rather than introducing a new database unless strictly necessary.

Minimum tables/entities:

```text
connectors
connector_capabilities
connector_health
flows
flow_nodes
flow_edges
flow_runs
node_runs
events
deliveries
secrets_metadata   # references only, no plaintext secret values
```

Every flow run and node run must have explicit status and timestamps.

## 11. Security

Mandatory:

- never return plaintext connector secrets from API;
- use `secret_ref` indirection;
- redact Authorization/cookies/query tokens in logs;
- SSRF protection for user-created HTTP/MCP endpoints;
- connector-level allow/deny policy for MCP tools;
- request body size limits;
- webhook secret rotation;
- timeout and cancellation;
- disable connector acts as a hard gate.

## 12. What not to build before P0 closes

Do not spend the next iteration on:

- Agent Runtime / RuntimeDispatcher expansion;
- message accumulation as a headline feature;
- new Slack/Telegram/Feishu-specific classes if generic webhook suffices;
- large WebUI redesign;
- complex replay UI;
- long-lived outbound WebSocket sessions;
- embedding/routing intelligence.

These can wait until the connector layer is usable.

## 13. Implementation sequence

### P0-A — Connector foundation

- connector schema + SQLite migration
- ConnectorRegistry
- generic driver interface
- health/status API
- secret reference contract

### P0-B — MCP connector

- Streamable HTTP MCP client
- server initialization/session lifecycle
- tools/resources/prompts discovery
- capability cache
- generic tool invocation
- GBrain as integration fixture

### P0-C — Webhook connectors

- generated inbound webhook connector
- reusable outbound webhook connector
- test endpoints
- retry/idempotency/redaction

### P0-D — Chains

- flow/node/edge persistence
- deterministic DAG execution
- connector trigger/action nodes
- filter + transform
- run/node-run inspection

### P0-E — End-to-end acceptance

Mandatory demo:

```text
Inbound Webhook
  -> MCP Connector (GBrain-compatible server)
  -> invoke one real tool
  -> transform output
  -> Outbound Webhook
```

Acceptance requires persisted event, flow run, node runs, delivery record, readback, error handling, and replayable provenance.

## 14. Definition of done

P0 is complete only when all of the following are true:

- [ ] Connector CRUD works.
- [ ] MCP Streamable HTTP connector can connect to a real MCP server.
- [ ] MCP discovery returns real tool schemas.
- [ ] MCP tool invocation works through the generic connector interface.
- [ ] Inbound webhook connectors can be created dynamically.
- [ ] Outbound webhook connectors can be created dynamically.
- [ ] Connectors can be chained without custom integration code.
- [ ] One flow can fan out to multiple connector actions.
- [ ] Full run/node/delivery state is persisted and inspectable.
- [ ] Secrets are not stored or returned in plaintext.
- [ ] GBrain-compatible MCP is used as the primary integration test.
- [ ] Unit/integration tests cover success, timeout, auth failure, malformed schema, connector disabled, tool error, outbound retry, and duplicate inbound event.

Only after this is green should PulseRelay resume lower-priority feature expansion.
