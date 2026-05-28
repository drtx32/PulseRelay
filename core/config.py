"""
Configuration loader for PulseRelay.

PulseRelay is moving away from environment-variable-centric configuration
toward structured runtime configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path("data/config.yaml")


class ConfigLoader:
    """Load structured YAML configuration."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else DEFAULT_CONFIG_PATH
        self.data: dict[str, Any] = {}

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict[str, Any]:
        if not self.exists():
            self.data = {}
            return self.data

        with open(self.path, "r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f) or {}

        return self.data

    def get(self, *keys: str, default=None):
        value: Any = self.data

        for key in keys:
            if not isinstance(value, dict):
                return default
            value = value.get(key)
            if value is None:
                return default

        return value
