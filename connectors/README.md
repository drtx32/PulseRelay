# Mounted connectors

Place one directory per trusted connector here. Each connector directory needs
a `connector.yaml` manifest and normally a `connector.py` script. The Compose
supervisor discovers these files automatically.

Polling connectors may declare `polling_interval_seconds` at the top level of
`connector.yaml`. The supervisor exposes that value to the process as
`POLL_INTERVAL_SECONDS`, so the connector does not need a duplicated interval
setting under `env`. When aggregation is enabled, keep
`aggregation.idle_timeout_seconds` greater than `polling_interval_seconds`;
the web UI flags the configuration when that relationship is invalid.

The script receives:

- `PULSERELAY_INGEST_URL`: `POST` normalized events here;
- `PULSERELAY_HOOK_URL`: optional generated hook URL from the manifest;
- `PULSERELAY_STATE_DIR`: persistent writable state/checkpoint directory;
- `PULSERELAY_LOG_FILE`: persistent log path;
- `PULSERELAY_CONNECTOR_ID`: connector id.

The mounted connector code is read-only. Write checkpoints and local state only
under `PULSERELAY_STATE_DIR`.

`gbrain/` is a standalone GBrain HTTP MCP connector. Configure either a static
`GBRAIN_MCP_TOKEN` or OAuth `GBRAIN_CLIENT_ID`/`GBRAIN_CLIENT_SECRET` in the
root `.env`, then enable its manifest. OAuth uses client credentials with the
`read` scope by default. Tool names and JSON arguments are configurable via
the `GBRAIN_*` variables in `.env.example`.

The verified GBrain schema uses `list_pages` with `type: task_run` for polling
and `get_page` with a required `slug` for reading output pages.

`my-source/` is a complete GitHub Releases polling example. It is disabled by
default; set `enabled: true` and change `GITHUB_REPOSITORY` before starting the
Compose connector service. For private repositories or higher API limits, add
`GITHUB_TOKEN` to the `pulserelay-connectors` service environment in
`docker-compose.yml`; do not put the token in `connector.yaml`.
