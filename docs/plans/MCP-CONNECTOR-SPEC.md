# PulseRelay Control Plane + Webhook Data Plane — P0 Implementation Spec

- **Status:** P0 / implementation priority
- **Audience:** Oracle Codex / maintainers
- **Repository:** `drtx32/PulseRelay`

## 1. Core product boundary

PulseRelay is **not an Agent Runtime** and **MCP is not an outbound delivery transport**.

The architecture is split into two planes:

```text
CONTROL PLANE
Remote MCP Client
    -> PulseRelay MCP Server
    -> inspect / create / update / connect configuration
    -> Connector Registry / Endpoints / Routes / Flows / Secrets metadata / Health

DATA PLANE
Inbound Endpoint / Source
    -> Normalize Event
    -> Route / Transform / Chain
    -> Webhook Outbound
    -> active push to destination
```

The key rule is:

> **MCP manages and operates the connection graph; Webhook Outbound carries proactive message delivery.**

PulseRelay must not model an MCP server as an outbound message destination. If a downstream system must receive a pushed message, it exposes or is wrapped by a webhook endpoint.

This preserves the intended behavior: **active push**, not passive downstream polling/tool-calling.

## 2. What MCP is for

PulseRelay must expose a **remote MCP control surface** so a remote agent/client can understand and modify how endpoints are connected.

The remote MCP client must be able to:

- list/get connectors/endpoints;
- create/update/disable connectors;
- create inbound webhook endpoints;
- create outbound webhook endpoints;
- create/update/delete routes/flows;
- connect one endpoint to another;
- inspect topology/graph;
- inspect route conditions, transforms and destinations;
- inspect health/status;
- inspect run/delivery history and failures;
- test an inbound endpoint;
- test an outbound webhook;
- rotate/regenerate webhook credentials via safe operations;
- validate a flow before enabling it.

MCP is therefore the **configuration and operations API for PulseRelay**, comparable to how GBrain exposes its internal business capabilities through MCP tools.

It is not part of the normal event-delivery path.

## 3. Reference pattern from GBrain

Borrow the architectural principle from GBrain:

```text
Business / configuration layer
        ^
        |
MCP tool layer
        ^
        |
Remote MCP transport
```

The MCP server should be thin. Tool handlers call the same internal services used by PulseRelay's HTTP/API/UI.

Do not duplicate connector, route or flow logic inside MCP handlers.

Example:

```text
create_route MCP tool
    -> RouteService.create(...)

POST /v1/routes
    -> RouteService.create(...)

Web UI "Create Route"
    -> RouteService.create(...)
```

One business implementation, multiple control surfaces.

## 4. Endpoint / Connector model

P0 requires persistent endpoint/connector definitions.

Minimum kinds:

- `webhook_inbound`
- `webhook_outbound`
- source/poller connectors already supported by PulseRelay where useful

Do **not** create `mcp_outbound`.

An MCP remote-control connection to PulseRelay itself is not represented as a data-plane destination connector.

Suggested generic endpoint shape:

```json
{
  "id": "ep_...",
  "name": "Multica Research",
  "kind": "webhook_outbound",
  "enabled": true,
  "config": {},
  "secret_refs": [],
  "metadata": {},
  "created_at": "...",
  "updated_at": "..."
}
```

## 5. PulseRelay MCP Server — highest priority

PulseRelay must itself expose an MCP server suitable for remote administration.

P0 transport:

- **Streamable HTTP** required.
- stdio optional for local development, but remote HTTP is the primary use case.

MCP tools should include at least:

### Endpoints / connectors

```text
list_connectors
get_connector
create_connector
update_connector
disable_connector
health_check_connector
```

### Inbound webhook

```text
create_webhook_inbound
get_webhook_inbound
rotate_webhook_inbound_secret
test_webhook_inbound
```

### Outbound webhook

```text
create_webhook_outbound
get_webhook_outbound
update_webhook_outbound
test_webhook_outbound
```

### Routes / flows

```text
list_routes
get_route
create_route
update_route
disable_route
validate_route
list_flows
get_flow
create_flow
update_flow
disable_flow
validate_flow
```

### Topology / observability

```text
get_topology
get_flow_run
list_flow_runs
get_delivery
list_deliveries
replay_delivery
```

Tool schemas must be explicit and stable enough for a remote agent to safely create a connection graph without guessing field names.

## 6. Webhook Inbound

Creating an inbound webhook endpoint through MCP/API/UI must dynamically generate a usable ingress URL.

Example:

```http
POST /v1/hooks/{endpoint_id}/{secret}
```

Requirements:

- generated credential stored securely / hashed as appropriate;
- request size/content-type limits;
- optional HMAC/signature verification;
- payload normalization/template mapping;
- stable event id and dedupe key;
- persist before downstream side effects;
- secret rotation;
- disabling endpoint immediately blocks ingress.

The MCP tool may return the newly generated URL/secret **only at creation/rotation time** where appropriate; later reads must not expose plaintext secret material.

## 7. Webhook Outbound — the only proactive outbound transport in P0

All proactive downstream message delivery is represented as `webhook_outbound`.

Example destination:

```json
{
  "id": "ep_multica",
  "kind": "webhook_outbound",
  "url": "https://example.com/hooks/...",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer ${secret:multica-token}"
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

Mandatory behavior:

- active HTTP push;
- durable delivery row;
- retry/backoff;
- idempotency key/header;
- timeout/cancellation;
- redacted logs;
- replay;
- independent fan-out lifecycle.

Do not implement MCP tool invocation as delivery.

If a target system currently exposes only MCP and needs to receive pushed events, create a small webhook receiver/bridge on that system side rather than making PulseRelay's outbound path passive.

## 8. Route / Flow model

The configuration graph must be remotely creatable through MCP.

Examples:

```text
Webhook Inbound: GitHub
    -> filter pull_request.opened
    -> transform payload
    -> Webhook Outbound: Multica
```

```text
GBrain Source/Poller
    -> route gbrain.page.updated
    -> transform summary envelope
    -> Webhook Outbound: downstream automation
```

```text
Webhook Inbound: source A
    -> condition
    -> Webhook Outbound: B
    -> Webhook Outbound: C
```

The remote MCP client configures these edges, but events do not travel through MCP.

Suggested flow model:

```json
{
  "id": "flow_...",
  "name": "gbrain-to-multica",
  "enabled": true,
  "nodes": [
    {"id": "n1", "type": "endpoint_trigger", "endpoint_id": "ep_in"},
    {"id": "n2", "type": "filter", "expression": "..."},
    {"id": "n3", "type": "transform", "template": "..."},
    {"id": "n4", "type": "webhook_outbound", "endpoint_id": "ep_out"}
  ],
  "edges": [["n1","n2"],["n2","n3"],["n3","n4"]]
}
```

P0 only needs deterministic sequential execution + simple fan-out.

Required node types:

- endpoint/source trigger
- filter
- transform
- webhook outbound
- emit event

Later:

- delay
- approval
- branch/switch
- aggregation

## 9. Control-plane API parity

MCP, HTTP API and Web UI should operate on the same services and persistent models.

The acceptance criterion is not merely "MCP server starts". A remote MCP client must be able to perform the full configuration lifecycle:

1. inspect current topology;
2. create inbound endpoint;
3. create outbound webhook endpoint;
4. create a route connecting them;
5. configure filter/transform;
6. validate route;
7. enable route;
8. send a sample inbound event;
9. observe active outbound push;
10. inspect flow-run/delivery result remotely through MCP.

## 10. Persistence

Extend current SQLite storage.

Minimum entities:

```text
connectors_or_endpoints
routes
flows
flow_nodes
flow_edges
flow_runs
node_runs
events
deliveries
connector_health
secrets_metadata
```

MCP sessions themselves do not need to become durable workflow nodes.

## 11. Security

Mandatory:

- authentication/authorization for remote MCP control plane;
- tool-level authorization for destructive/configuration operations;
- never expose stored plaintext secrets;
- secret refs + redaction;
- SSRF protection for outbound webhook URLs;
- request body limits;
- timeout/cancellation;
- secret rotation;
- audit who changed which route/endpoint through MCP/API/UI;
- disabling a connector/route is a hard gate.

## 12. Explicit non-goals for P0

Do not prioritize:

- MCP as outbound delivery;
- remote MCP tool invocation as event delivery;
- Agent Runtime / RuntimeDispatcher expansion;
- message accumulation as a headline feature;
- new destination-specific adapters when webhook is enough;
- large WebUI redesign;
- outbound WebSocket sessions;
- complex BPMN/workflow engine.

## 13. Implementation sequence

### P0-A — Shared configuration services

- endpoint/connector persistence
- route/flow persistence
- shared service layer
- health/status
- secret-reference contract

### P0-B — Remote MCP control plane

- Streamable HTTP MCP server
- MCP auth
- explicit tools for endpoint/route/flow CRUD
- topology inspection
- validation tools
- run/delivery inspection tools
- all handlers call shared services

### P0-C — Webhook endpoints

- dynamic inbound webhook creation
- reusable outbound webhook creation
- retry/idempotency/redaction
- test operations

### P0-D — Flow execution

- deterministic DAG execution
- filter + transform
- webhook outbound nodes
- fan-out
- persisted run/node/delivery state

### P0-E — Mandatory end-to-end demo

From a **remote MCP client**:

```text
MCP: create inbound webhook A
MCP: create outbound webhook B
MCP: create route A -> transform -> B
MCP: validate + enable route
```

Then on the data plane:

```text
POST event -> inbound webhook A
           -> route/transform
           -> active HTTP push -> webhook B
```

Finally from MCP:

```text
inspect flow_run / node_runs / delivery
```

This is the canonical P0 demo.

## 14. Definition of done

P0 is complete only when:

- [ ] PulseRelay exposes a remotely usable Streamable HTTP MCP server.
- [ ] Remote MCP can inspect the current endpoint/route topology.
- [ ] Remote MCP can create/update/disable inbound and outbound webhook endpoints.
- [ ] Remote MCP can create/update/validate/enable routes/flows.
- [ ] Outbound delivery is webhook-only for proactive push.
- [ ] No MCP-outbound destination type exists in the data plane.
- [ ] A remote MCP client can configure an end-to-end route without editing source/config files manually.
- [ ] A real inbound event causes an active outbound HTTP webhook push.
- [ ] Flow/node/delivery state is persisted and inspectable through MCP.
- [ ] Secrets are protected and redacted.
- [ ] Tests cover auth, CRUD, validation, disabled routes, duplicate inbound, outbound retry, timeout, secret redaction, sequential flow and fan-out.

Only after this is green should lower-priority feature expansion resume.
