## Docker deployment

The repository now includes a Docker image and Compose service for the
webhook-native API plus a delivery worker. The API runs `api:create_app`, the
worker consumes durable outbound webhook jobs, and both share SQLite data in a
named volume.

Compose also starts `pulserelay-connectors`. It mounts the host's
`./connectors` directory read-only and discovers every
`connectors/*/connector.yaml`. Connector logs and supervisor state are stored
in the separate `pulserelay-connector-state` volume. A connector's own
checkpoints should be written below the injected `PULSERELAY_STATE_DIR`.

Start it with:

```bash
cp .env.example .env
# Edit GITHUB_REPOSITORY and, if needed, GITHUB_TOKEN in .env.
docker compose up -d --build
curl http://localhost:8000/healthz
```

Create a generated inbound hook:

```bash
curl -X POST http://localhost:8000/v1/hooks \
  -H 'content-type: application/json' \
  -d '{"id":"demo","name":"Demo source"}'
```

The response contains the secret-bearing hook URL once. Store it in the
calling system's secret manager; it is not returned by subsequent list/get
operations. SQLite data survives container replacement in the
`pulserelay-data` volume.

The worker automatically claims pending/retrying deliveries, honors leases and
retry schedules, and continues after API/container restarts. The API's
`POST /v1/deliveries/process` endpoint remains available for one-shot/manual
processing, but is not needed in the Compose setup.

To add a connector, create for example:

```text
connectors/my-source/connector.yaml
connectors/my-source/connector.py
```

with:

```yaml
id: my-source
command: ["python", "/connectors/my-source/connector.py"]
restart: always
```

The supervisor automatically starts it and restarts it after exit. The script
can use `PULSERELAY_INGEST_URL` to submit normalized `EventEnvelope` JSON. The
example connector is disabled by default. Mounted scripts are arbitrary code;
only mount trusted code and keep secrets in environment/secret management, not
in the connector manifest.

Compose loads the optional root `.env` through `env_file` for the connector
supervisor. The supervisor expands `${GITHUB_REPOSITORY}` in the manifest and
passes `GITHUB_TOKEN` through to the connector process. The actual `.env` file
is ignored by Git and Docker build context.

Stop the service with:

```bash
docker compose down
```

There is one supported runtime now: the webhook-native API, delivery worker,
and connector supervisor started by Compose.
