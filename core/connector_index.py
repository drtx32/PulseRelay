"""Cached mounted-connector manifest index.

Connector manifests are configuration, not request data. Parse them once at
startup and only rebuild the affected index when the manifest fingerprint
changes. The index is also persisted so the parsed representation is useful to
diagnostics and future processes without reparsing unchanged YAML.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

import yaml


class ConnectorManifestIndex:
    def __init__(self, root: str | Path, cache_path: str | Path = "/data/connectors.index.json"):
        self.root = Path(root)
        self.cache_path = Path(cache_path)
        self._lock = RLock()
        self._fingerprint: list[dict[str, Any]] | None = None
        self._items: list[dict[str, Any]] = []

    def _paths_and_fingerprint(self) -> tuple[list[Path], list[dict[str, Any]]]:
        if not self.root.is_dir():
            return [], []
        paths = sorted(path for path in self.root.glob("*/connector.yaml") if path.is_file())
        fingerprint = []
        for path in paths:
            stat = path.stat()
            fingerprint.append({"path": str(path.relative_to(self.root)), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size})
        return paths, fingerprint

    def _build(self, paths: list[Path]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in paths:
            try:
                content = path.read_text(encoding="utf-8")[:128 * 1024]
                parsed = yaml.safe_load(content) or {}
                if not isinstance(parsed, dict):
                    raise ValueError("connector manifest must be a mapping")
                connector_id = str(parsed.get("id") or path.parent.name)
                polling = parsed.get("polling_interval_seconds", parsed.get("poll_interval_seconds", 60))
                items.append({
                    "id": connector_id,
                    "name": connector_id,
                    "manifest": content,
                    "manifest_json": parsed,
                    "polling_interval_seconds": polling,
                })
            except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
                items.append({"id": path.parent.name, "name": path.parent.name,
                              "manifest": f"<unable to parse connector.yaml: {exc}>",
                              "manifest_json": {}, "error": str(exc)})
        return items

    def _persist(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix="connectors.index.", suffix=".tmp", dir=self.cache_path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "fingerprint": self._fingerprint, "connectors": self._items}, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self.cache_path)
        except OSError:
            # A cache is an optimization; a read-only mount must not break API startup.
            try:
                os.unlink(temporary)
            except (OSError, UnboundLocalError):
                pass

    def refresh(self, force: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            paths, fingerprint = self._paths_and_fingerprint()
            if not force and self._fingerprint == fingerprint:
                return self._items
            self._fingerprint = fingerprint
            self._items = self._build(paths)
            self._persist()
            return self._items

    def items(self) -> list[dict[str, Any]]:
        return self.refresh()

    def find(self, source_type: str) -> dict[str, Any] | None:
        candidates = {source_type, source_type.replace("_", "-")}
        return next((item for item in self.items() if item.get("id") in candidates), None)


_indexes: dict[str, ConnectorManifestIndex] = {}
_indexes_lock = RLock()


def get_connector_index(root: str | Path, cache_path: str | Path = "/data/connectors.index.json") -> ConnectorManifestIndex:
    key = f"{Path(root).resolve()}::{Path(cache_path).resolve()}"
    with _indexes_lock:
        if key not in _indexes:
            _indexes[key] = ConnectorManifestIndex(root, cache_path)
        return _indexes[key]
