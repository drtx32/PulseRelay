"""Discover and supervise trusted connectors mounted into the container.

Each connector directory contains a ``connector.yaml`` manifest and usually a
``connector.py`` script. The scripts are intentionally external to PulseRelay:
the supervisor provides lifecycle management, durable state paths, logging and
the common event-ingress environment.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ConnectorSpec:
    id: str
    directory: Path
    command: list[str]
    enabled: bool = True
    restart: str = "always"
    restart_delay_seconds: float = 2.0
    env: dict[str, str] = field(default_factory=dict)
    working_dir: str | None = None
    hook_url: str = ""
    webhooks: list[dict[str, Any]] = field(default_factory=list)
    watch_paths: list[str] = field(default_factory=list)
    ignore_paths: list[str] = field(default_factory=list)
    polling_interval_seconds: float = 60.0

    @classmethod
    def from_file(cls, path: Path) -> "ConnectorSpec":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict): raise ValueError("connector manifest must be a mapping")
        connector_id = str(data.get("id") or path.parent.name)
        if not connector_id or "/" in connector_id or ".." in connector_id:
            raise ValueError("invalid connector id")
        command = data.get("command")
        if command is None:
            command = ["python", str(path.parent / "connector.py")]
        elif isinstance(command, str):
            command = shlex.split(command)
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            raise ValueError("connector command must be a string or list of strings")
        env = data.get("env") or {}
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, (str, int, float, bool)) for k, v in env.items()):
            raise ValueError("connector env must be a mapping of scalar values")
        def path_patterns(name: str) -> list[str]:
            value = data.get(name, [])
            if value is None: return []
            if isinstance(value, str): value = [value]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{name} must be a string or list of strings")
            return [item for item in value if item]
        raw_webhooks = data.get("webhooks", []) or []
        if not isinstance(raw_webhooks, list):
            raise ValueError("webhooks must be a list")
        webhooks: list[dict[str, Any]] = []
        for item in raw_webhooks:
            if isinstance(item, str): item = {"url": item}
            if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not item["url"].strip():
                raise ValueError("each webhook must have a url")
            webhooks.append({"name": str(item.get("name") or item["url"]), "url": item["url"].strip(), "enabled": bool(item.get("enabled", True))})
        polling_interval = data.get("polling_interval_seconds", data.get("poll_interval_seconds", 60))
        try:
            polling_interval = max(0.1, float(polling_interval))
        except (TypeError, ValueError) as exc:
            raise ValueError("polling_interval_seconds must be a positive number") from exc
        return cls(id=connector_id, directory=path.parent, command=[os.path.expandvars(item) for item in command],
            enabled=bool(data.get("enabled", True)), restart=str(data.get("restart", "always")),
            restart_delay_seconds=max(0.1, float(data.get("restart_delay_seconds", 2))),
            env={key: os.path.expandvars(str(value)) for key, value in env.items()},
            working_dir=str(data["working_dir"]) if data.get("working_dir") else None,
            hook_url=str(os.path.expandvars(data.get("hook_url", ""))), webhooks=webhooks,
            watch_paths=path_patterns("watch_paths"), ignore_paths=path_patterns("ignore_paths"),
            polling_interval_seconds=polling_interval)


class ConnectorSupervisor:
    def __init__(self, connector_root: str | Path = "/connectors", state_root: str | Path = "/var/lib/pulserelay/connectors",
                 api_url: str | None = None, poll_interval: float = 2.0):
        self.connector_root = Path(connector_root)
        self.state_root = Path(state_root)
        self.api_url = (api_url or os.getenv("PULSERELAY_API_URL", "http://pulserelay:8000")).rstrip("/")
        self.poll_interval = max(0.1, poll_interval)
        self.processes: dict[str, tuple[ConnectorSpec, subprocess.Popen, Any]] = {}
        self.next_restart: dict[str, float] = {}
        self.stopping = False

    def discover(self) -> dict[str, ConnectorSpec]:
        specs: dict[str, ConnectorSpec] = {}
        if not self.connector_root.exists(): return specs
        for manifest in sorted(self.connector_root.glob("*/connector.yaml")):
            try:
                spec = ConnectorSpec.from_file(manifest)
                if spec.id in specs: raise ValueError("duplicate connector id")
                specs[spec.id] = spec
            except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
                print(f"[connector-supervisor] ignoring {manifest}: {exc}", flush=True)
        return specs

    def _state_dir(self, connector_id: str) -> Path:
        path = self.state_root / connector_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_state(self, connector_id: str, **fields: Any) -> None:
        path = self._state_dir(connector_id) / "supervisor.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def start(self, spec: ConnectorSpec) -> bool:
        if not spec.enabled or spec.id in self.processes or time.monotonic() < self.next_restart.get(spec.id, 0): return False
        state_dir = self._state_dir(spec.id)
        log_path = state_dir / "connector.log"
        environment = os.environ.copy()
        environment.update(spec.env)
        environment.update({"PULSERELAY_WATCH_PATHS_JSON": json.dumps(spec.watch_paths),
                            "PULSERELAY_IGNORE_PATHS_JSON": json.dumps(spec.ignore_paths),
                            "PULSERELAY_WEBHOOKS_JSON": json.dumps(spec.webhooks, ensure_ascii=False),
                            "POLL_INTERVAL_SECONDS": str(spec.polling_interval_seconds)})
        environment.update({"PULSERELAY_CONNECTOR_ID": spec.id, "PULSERELAY_API_URL": self.api_url,
            "PULSERELAY_INGEST_URL": f"{self.api_url}/v1/events", "PULSERELAY_STATE_DIR": str(state_dir),
            "PULSERELAY_LOG_FILE": str(log_path)})
        if spec.hook_url: environment["PULSERELAY_HOOK_URL"] = spec.hook_url
        cwd = Path(spec.working_dir) if spec.working_dir else spec.directory
        if not cwd.is_absolute(): cwd = spec.directory / cwd
        log = log_path.open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(spec.command, cwd=str(cwd), env=environment, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except (OSError, ValueError):
            log.close()
            raise
        self.processes[spec.id] = (spec, process, log)
        self._write_state(spec.id, id=spec.id, pid=process.pid, status="running", command=spec.command,
                          started_at=time.time(), log_file=str(log_path))
        return True

    def reap(self) -> None:
        for connector_id, (spec, process, log) in list(self.processes.items()):
            return_code = process.poll()
            if return_code is None: continue
            log.close()
            self.processes.pop(connector_id, None)
            self._write_state(connector_id, id=connector_id, pid=process.pid, status="exited", return_code=return_code,
                              exited_at=time.time(), restart=spec.restart)
            if spec.restart == "always" or (spec.restart == "on-failure" and return_code != 0):
                self.next_restart[connector_id] = time.monotonic() + spec.restart_delay_seconds
            else:
                self.next_restart[connector_id] = float("inf")

    def stop_all(self) -> None:
        self.stopping = True
        for connector_id, (_, process, log) in list(self.processes.items()):
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
            finally:
                log.close()
                self.processes.pop(connector_id, None)
                self._write_state(connector_id, id=connector_id, pid=process.pid, status="stopped", stopped_at=time.time())

    def run(self) -> None:
        while not self.stopping:
            specs = self.discover()
            for connector_id, (spec, process, _) in list(self.processes.items()):
                if connector_id not in specs or specs[connector_id] != spec:
                    try: os.killpg(process.pid, signal.SIGTERM)
                    except OSError: pass
            self.reap()
            for spec in specs.values():
                try: self.start(spec)
                except (OSError, ValueError) as exc: print(f"[connector-supervisor] failed to start {spec.id}: {exc}", flush=True)
            time.sleep(self.poll_interval)


def main() -> None:
    supervisor = ConnectorSupervisor(
        connector_root=os.getenv("PULSERELAY_CONNECTOR_ROOT", "/connectors"),
        state_root=os.getenv("PULSERELAY_CONNECTOR_STATE_ROOT", "/var/lib/pulserelay/connectors"),
    )
    signal.signal(signal.SIGTERM, lambda *_: supervisor.stop_all())
    signal.signal(signal.SIGINT, lambda *_: supervisor.stop_all())
    supervisor.run()


if __name__ == "__main__": main()
