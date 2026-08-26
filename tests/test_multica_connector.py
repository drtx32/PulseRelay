import connectors.multica.connector as multica_connector
from connectors.multica.connector import normalize_run


def test_normalize_run_emits_a_pulserelay_event():
    event = normalize_run(
        "auto-123",
        {
            "id": "run-456",
            "status": "completed",
            "title": "Daily summary",
            "completed_at": "2026-08-26T12:34:56+08:00",
            "summary": "Delivered",
        },
    )

    assert event["source"] == {"type": "multica", "id": "auto-123", "name": "Multica"}
    assert event["event"]["type"] == "multica.autopilot.run.completed"
    assert "Daily summary" in event["content"]["text"]
    assert "2026-08-26 04:34:56 UTC" in event["content"]["text"]
    assert event["context"]["extra"]["run_id"] == "run-456"


def test_unset_runs_url_placeholder_uses_default_endpoint(monkeypatch):
    monkeypatch.setattr(multica_connector, "MULTICA_API_URL", "https://multica.example")
    monkeypatch.setenv("MULTICA_AUTOPILOT_RUNS_URL", "${MULTICA_AUTOPILOT_RUNS_URL}")

    assert multica_connector._runs_url("auto/123") == "https://multica.example/api/autopilots/auto%2F123/runs?limit=100"
