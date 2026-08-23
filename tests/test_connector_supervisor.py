from __future__ import annotations

import json
import sys
import time

from core.connector_supervisor import ConnectorSpec, ConnectorSupervisor


def test_manifest_defaults_to_connector_script_and_validates_command(tmp_path):
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "connector.yaml").write_text("id: source\nenabled: true\n", encoding="utf-8")
    (directory / "connector.py").write_text("pass\n", encoding="utf-8")
    spec = ConnectorSpec.from_file(directory / "connector.yaml")
    assert spec.command == ["python", str(directory / "connector.py")]
    assert spec.id == "source"


def test_supervisor_persists_state_and_injects_connector_environment(tmp_path):
    root = tmp_path / "connectors"
    directory = root / "source"
    directory.mkdir(parents=True)
    script = directory / "connector.py"
    script.write_text(
        "import os, pathlib\n"
        "p = pathlib.Path(os.environ['PULSERELAY_STATE_DIR'])\n"
        "p.joinpath('checkpoint.txt').write_text(os.environ['PULSERELAY_INGEST_URL'])\n",
        encoding="utf-8",
    )
    (directory / "connector.yaml").write_text(
        f"id: source\nrestart: never\ncommand: [{json.dumps(sys.executable)}, {json.dumps(str(script))}]\n",
        encoding="utf-8",
    )
    state_root = tmp_path / "state"
    supervisor = ConnectorSupervisor(root, state_root, api_url="http://relay:8000", poll_interval=0.1)
    spec = supervisor.discover()["source"]
    assert supervisor.start(spec)
    process = supervisor.processes["source"][1]
    assert process.wait(timeout=3) == 0
    supervisor.reap()
    assert (state_root / "source" / "checkpoint.txt").read_text() == "http://relay:8000/v1/events"
    state = json.loads((state_root / "source" / "supervisor.json").read_text())
    assert state["status"] == "exited"
    assert state["restart"] == "never"
    assert "source" not in supervisor.processes


def test_supervisor_discovers_only_valid_manifests(tmp_path):
    root = tmp_path / "connectors"
    (root / "good").mkdir(parents=True)
    (root / "good" / "connector.yaml").write_text("id: good\ncommand: ['echo', 'ok']\n", encoding="utf-8")
    (root / "bad").mkdir()
    (root / "bad" / "connector.yaml").write_text("id: ../escape\n", encoding="utf-8")
    specs = ConnectorSupervisor(root, tmp_path / "state").discover()
    assert list(specs) == ["good"]


def test_manifest_loads_watch_and_ignore_paths(tmp_path):
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "connector.yaml").write_text(
        "id: source\nwatch_paths: [notes/**]\nignore_paths: ['**/*plan*']\n", encoding="utf-8"
    )
    spec = ConnectorSpec.from_file(directory / "connector.yaml")
    assert spec.watch_paths == ["notes/**"]
    assert spec.ignore_paths == ["**/*plan*"]


def test_manifest_exposes_polling_interval_without_connector_aggregation(tmp_path):
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "connector.yaml").write_text(
        "id: source\npolling_interval_seconds: 17\naggregation:\n  max_events: 3\n",
        encoding="utf-8",
    )
    spec = ConnectorSpec.from_file(directory / "connector.yaml")
    assert spec.polling_interval_seconds == 17
    assert not hasattr(spec, "aggregation")
