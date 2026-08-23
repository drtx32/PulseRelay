# PulseRelay

PulseRelay is a durable event gateway for automation systems. It accepts
normalized events from mounted connectors or inbound webhook hooks, persists
them before side effects, matches routes, aggregates events, and delivers
independent outbound webhook jobs with retries, leases, idempotency keys,
audit logs, and dead letters.

```text
mounted connector / inbound hook
        -> durable event store
        -> route + aggregation
        -> delivery jobs
        -> outbound webhook worker
```

## Run with Docker

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8000/healthz
```

Compose starts:

- `pulserelay`: HTTP API and generated inbound hook management;
- `pulserelay-worker`: durable outbound webhook worker;
- `pulserelay-connectors`: supervisor for scripts mounted from `./connectors`.

The root URL serves the lightweight Paper-style Event Desk. Enter the value of
`PULSERELAY_ADMIN_TOKEN` as the PulseRelay admin Bearer token. Event reads
at `/v1/events` and `/v1/events/{event_id}` require that Bearer credential;
`/healthz` remains public for uptime checks.

SQLite data is stored in the `pulserelay-data` volume. Connector logs,
supervisor state, and connector checkpoints are stored in
`pulserelay-connector-state`.

## Add a connector

Create a directory containing `connector.yaml` and a script:

```text
connectors/my-source/
├── connector.yaml
└── connector.py
```

The supervisor starts enabled manifests automatically, restarts failed
processes, and injects:

- `PULSERELAY_INGEST_URL` — normalized event endpoint;
- `PULSERELAY_HOOK_URL` — optional generated hook URL;
- `PULSERELAY_STATE_DIR` — persistent writable state directory;
- `PULSERELAY_LOG_FILE` — persistent connector log path;
- `PULSERELAY_CONNECTOR_ID` — connector identifier.

See `connectors/my-source/` for a working GitHub Releases polling connector and
`connectors/gbrain/` for the HTTP MCP polling connector.
It is disabled by default; set `enabled: true` after configuring the
repository in `.env`.

## Inbound hooks

Generate a hook:

```bash
curl -X POST http://localhost:8000/v1/hooks \
  -H 'content-type: application/json' \
  -d '{"id":"demo","name":"Demo source"}'
```

The generated secret-bearing URL is returned once. Secrets are stored as
salted PBKDF2 verifiers. Requests support body limits, content-type allowlists,
optional HMAC timestamp validation, deterministic duplicate handling, and
sandboxed normalization/response templates.

See `docs/docker.md` and `docs/webhook-native.md` for configuration and API
details.

## Development

Install `requirements.txt`, then run the tests with pytest. The supported
runtime entrypoint is `api:create_app`; Docker Compose is the recommended
local deployment.
