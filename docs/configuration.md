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
```

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
