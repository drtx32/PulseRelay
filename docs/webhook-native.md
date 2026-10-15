## Webhook-native relay

The durable core is available through `api.create_app(store)` and the
`DurableRelay` service. Inbound events are written before route evaluation;
routes create durable batches and one independent delivery row per destination.

Minimal setup:

```python
from core.persistence import SQLitePhase9Store
from api import create_app

store = SQLitePhase9Store("data/pulserelay.db")
app = create_app(store)
```

Configure an outbound webhook with `POST /v1/webhooks`, then a route with
`POST /v1/routes`. Set `config.allow_private: true` only for explicitly trusted
local development endpoints. Delivery retries use the same idempotency key and
claimed rows have an expiring lease, so a worker restart does not lose work.

The GBrain connector lives under `connectors/gbrain/` and runs as a standalone
mounted script. It speaks MCP Streamable HTTP JSON-RPC using
`GBRAIN_MCP_URL` and `GBRAIN_MCP_TOKEN`, and persists its cursor and dedupe set
under `PULSERELAY_STATE_DIR`. The default tool names are `query` and `get_page`;
override them and their JSON arguments with the `GBRAIN_*` variables when the
GBrain deployment exposes a different schema.

### Generated inbound hooks

Create a hook with `POST /v1/hooks`:

```json
{
  "id": "gbrain-production",
  "name": "GBrain Production",
  "source_type": "gbrain",
  "max_body_bytes": 1048576,
  "allowed_content_types": ["application/json"]
}
```

The response contains a generated secret and the complete URL exactly once:
`/v1/hooks/{endpoint_id}/{secret}`. Only a salted PBKDF2-SHA256 verifier is persisted;
listing or updating a hook never returns the secret. Rotate it with
`PUT /v1/hooks/{endpoint_id}` and `{ "rotate_secret": true }`.

The generated inbound URL supports `POST`, `PUT`, `PATCH`, and `DELETE` for JSON
event submission, and `GET` for challenge verification. These four event methods
share the same content-type, size, HMAC, normalization, persistence, and response
behavior. For GET, provide exactly one of these query parameters:
`challenge`, `echostr`, `validationToken`, or `hub.challenge`. Unrelated query
parameters (such as provider timestamps or signatures) are ignored. PulseRelay
validates that the endpoint is enabled and the URL secret is correct, then
returns the decoded challenge value verbatim as `text/plain; charset=utf-8`
(HTTP 200). Missing, empty, oversized (>4096 characters), repeated, or
ambiguous challenge parameters return HTTP 400; an unknown, disabled, or
incorrect path secret returns HTTP 404. GET verification does not
create or enqueue an event. This is an echo-challenge flow; providers requiring
their own cryptographic signature/token verification must perform that check
separately or use a provider-specific adapter.
`HEAD` validates the endpoint and returns an empty HTTP 200 for availability
checks. `OPTIONS` validates the endpoint and returns HTTP 204 with an `Allow`
header; neither method creates an event. `TRACE` and `CONNECT` are not supported.

Hooks reject disabled/unknown secrets, unsupported content types, oversized
bodies, and malformed JSON. Duplicate events return `2xx` with the original
`event_id` and `duplicate: true`. For third-party HMAC webhooks, configure
`hmac_secret_ref` to an environment variable. The request must include
`X-PulseRelay-Timestamp` and `X-PulseRelay-Signature`; the signed message is
`{timestamp}.{raw_body}` and stale or non-finite timestamps are rejected. The
body is consumed incrementally and a request without a trustworthy
`Content-Length` cannot bypass the configured byte limit.

Optional `normalization_template` and `response_template` values use Jinja's
sandboxed environment and cannot access Python imports, files, processes, or
network APIs. Templates are capped in source/output size and loops, macros,
calls, imports, includes, and inheritance are rejected.

Mounted connectors submit normalized events to the durable API. Outbound
business protocols should be implemented as HTTP webhook consumers, not added
as new core delivery types.
