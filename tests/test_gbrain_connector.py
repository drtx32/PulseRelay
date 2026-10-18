from connectors.gbrain import connector
from connectors.gbrain.connector import LIST_RUNS_TOOL, MCPHTTPClient


def _run(index: int) -> dict:
    return {
        "id": f"run-{index}",
        "updated_at": f"2026-08-27T00:{index // 60:02d}:{index % 60:02d}.000Z",
    }


def test_list_runs_uses_updated_after_and_fetches_all_pages(monkeypatch):
    monkeypatch.setenv(
        "GBRAIN_LIST_RUNS_ARGS_JSON",
        '{"type":"task_run","sort":"updated_asc","limit":100}',
    )
    client = object.__new__(MCPHTTPClient)
    first_page = [_run(index) for index in range(100)]
    second_page = [_run(100)]
    calls = []

    def call_tool(name, arguments):
        calls.append((name, dict(arguments)))
        return {"items": first_page if "updated_after" not in arguments else second_page}

    client.call_tool = call_tool

    assert client.list_runs() == first_page + second_page
    assert calls == [
        (LIST_RUNS_TOOL, {"type": "task_run", "sort": "updated_asc", "limit": 100}),
        (
            LIST_RUNS_TOOL,
            {
                "type": "task_run",
                "sort": "updated_asc",
                "limit": 100,
                "updated_after": first_page[-1]["updated_at"],
            },
        ),
    ]


def test_ingest_uses_scoped_bearer_token(monkeypatch):
    monkeypatch.setattr(connector, "INGEST_URL", "http://relay/v1/events")
    monkeypatch.setattr(connector, "INGEST_TOKEN", "ingest-token")
    captured = {}

    class Response:
        status = 200

        def __enter__(self): return self
        def __exit__(self, *args): return False

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(connector, "urlopen", fake_urlopen)
    connector.publish({"id": "event-1"})
    assert captured["request"].get_header("Authorization") == "Bearer ingest-token"


def test_list_runs_maps_checkpoint_to_updated_after(monkeypatch):
    monkeypatch.setenv("GBRAIN_LIST_RUNS_ARGS_JSON", '{"type":"task_run","limit":100}')
    client = object.__new__(MCPHTTPClient)
    calls = []

    def call_tool(name, arguments):
        calls.append((name, dict(arguments)))
        return {"items": []}

    client.call_tool = call_tool
    cursor = {"updated_at": "2026-08-26T04:11:00.000Z", "tie_breaker": "run-99"}

    assert client.list_runs(after=cursor) == []
    assert calls == [
        (
            LIST_RUNS_TOOL,
            {
                "type": "task_run",
                "sort": "updated_asc",
                "limit": 100,
                "updated_after": "2026-08-26T04:11:00.000Z",
            },
        )
    ]
