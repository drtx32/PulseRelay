# Configuration

PulseRelay is transitioning away from environment-variable-centric configuration.

The goal is:

- structured runtime config
- local secrets
- UI-managed settings

instead of many environment variables.

## Configuration Philosophy

Environment variables are best for:

- deployment-level overrides
- container/runtime integration
- CI systems

They are not ideal for:

- source definitions
- routing policies
- trigger settings
- delivery rules
- templates
- plugin configuration

PulseRelay therefore moves toward structured YAML configuration.

## Example Structure

config/
  sources.example.yaml

data/
  config.yaml

## Example Source Configuration

```yaml
sources:
  weflow:
    enabled: true
    type: weflow

    connection:
      host: localhost
      port: 5031
      access_token: "<configure-locally>"

    monitor:
      chats: []

    trigger:
      content_threshold: 1000
      message_threshold: 10
      idle_timeout: 20

  telegram:
    enabled: false
    connection:
      bot_token: "<telegram_bot_token>"
    monitor:
      allowed_chat_ids: []

  slack:
    enabled: false
    connection:
      app_token: "<xapp-token>"
      bot_token: "<xoxb-token>"
    monitor:
      allowed_channels: []

  github_webhook:
    enabled: false
    webhook:
      path: /webhooks/github
      secret: "<github_webhook_secret>"

phase9:
  enabled: true
  db_path: data/pulserelay_phase9.db
```

## Phase 9 API Endpoints

When `phase9.enabled` is true, gateway exposes:

- `GET /api/phase9/events`
- `GET /api/phase9/dead-letters`
- `GET /api/phase9/audit-logs`
- `POST /api/phase9/replay`

## Why YAML

Structured config enables:

- nested settings
- runtime validation
- UI generation
- plugin configuration
- dynamic reload
- multi-source orchestration

without requiring dozens of environment variables.

## Current State

Current migration stage:

- Event-native architecture established
- Configuration migration in progress
