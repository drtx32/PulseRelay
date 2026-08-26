"""Publish Multica Autopilot run completions into PulseRelay."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def _configured_env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default).strip()
    return default if value.startswith("${") and value.endswith("}") else value


MULTICA_API_URL = _configured_env("MULTICA_API_URL", "https://multica.ai").rstrip("/")
MULTICA_API_TOKEN = (_configured_env("MULTICA_API_TOKEN") or _configured_env("MULTICA_API_KEY")).strip()
AUTOPILOT_IDS = [item.strip() for item in _configured_env("MULTICA_AUTOPILOT_IDS").split(",") if item.strip()]
INGEST_URL = _configured_env("PULSERELAY_INGEST_URL")
INTERVAL = max(5, int(float(os.environ.get("POLL_INTERVAL_SECONDS", "60"))))
STATE_DIR = Path(os.environ.get("PULSERELAY_STATE_DIR", ".state"))
STATE_FILE = STATE_DIR / "checkpoint.json"
TERMINAL_STATUSES = {
    "completed", "complete", "succeeded", "success", "failed", "cancelled", "canceled", "skipped", "blocked"
}


def load_checkpoint() -> dict[str, str]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        seen = value.get("seen", {}) if isinstance(value, dict) else {}
        return {str(key): str(item) for key, item in seen.items()} if isinstance(seen, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        return {}


def save_checkpoint(seen: dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"seen": dict(list(seen.items())[-2000:])}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def _runs_url(autopilot_id: str) -> str:
    template = _configured_env("MULTICA_AUTOPILOT_RUNS_URL")
    if template:
        return template.format(autopilot_id=quote(autopilot_id, safe=""))
    return f"{MULTICA_API_URL}/api/autopilots/{quote(autopilot_id, safe='')}/runs?limit=100"


def list_runs(autopilot_id: str) -> list[dict]:
    if not MULTICA_API_TOKEN:
        raise RuntimeError("MULTICA_API_TOKEN is required")
    request = Request(_runs_url(autopilot_id), headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {MULTICA_API_TOKEN}",
        "User-Agent": "PulseRelay-multica-connector",
    }, method="GET")
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("runs", "items", "data", "results"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    raise RuntimeError("Multica autopilot runs response was not a list")


def _run_id(run: dict) -> str:
    return str(run.get("id") or run.get("run_id") or run.get("execution_id") or "")


def _status(run: dict) -> str:
    return str(run.get("status") or run.get("state") or "").strip().lower()


def _run_time(run: dict) -> str:
    return str(run.get("completed_at") or run.get("finished_at") or run.get("updated_at") or run.get("created_at") or "")


def _human_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError):
        return value


def normalize_run(autopilot_id: str, run: dict) -> dict:
    run_id = _run_id(run)
    status = _status(run)
    completed_at = _run_time(run)
    title = str(run.get("autopilot_title") or run.get("title") or autopilot_id)
    summary = str(run.get("summary") or run.get("result") or run.get("error") or "")
    text = (f"Multica Automation 完成\n自动化：{title}\n状态：{status}\nRun ID：{run_id}\n"
            f"时间：{_human_time(completed_at)}")
    if summary:
        text += f"\n\n{summary}"
    return {
        "id": f"multica:{autopilot_id}:{run_id}:{status}",
        "source": {"type": "multica", "id": autopilot_id, "name": "Multica"},
        "sender": {"id": "multica", "name": "Multica Automation", "role": "system", "trust_level": "system"},
        "event": {"type": f"multica.autopilot.run.{status}", "action": status,
                  "timestamp": completed_at or datetime.now(timezone.utc).isoformat(),
                  "dedupe_key": f"multica:{autopilot_id}:{run_id}:{status}:{completed_at}"},
        "content": {"title": title, "text": text, "raw": {"autopilot_id": autopilot_id, "run": run}},
        "context": {"extra": {"autopilot_id": autopilot_id, "run_id": run_id}},
        "routing": {"priority": "normal", "labels": ["multica", "automation", status]},
    }


def publish(event: dict) -> None:
    if not INGEST_URL:
        raise RuntimeError("PULSERELAY_INGEST_URL is required")
    request = Request(INGEST_URL, data=json.dumps(event, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=30) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"PulseRelay rejected event with HTTP {response.status}")


def poll_once() -> int:
    if not AUTOPILOT_IDS:
        raise RuntimeError("MULTICA_AUTOPILOT_IDS is required")
    seen = load_checkpoint()
    emitted = 0
    for autopilot_id in AUTOPILOT_IDS:
        for run in sorted(list_runs(autopilot_id), key=lambda item: _run_time(item)):
            run_id = _run_id(run)
            status = _status(run)
            if not run_id or status not in TERMINAL_STATUSES:
                continue
            marker = f"{status}:{_run_time(run)}"
            key = f"{autopilot_id}:{run_id}"
            if seen.get(key) == marker:
                continue
            publish(normalize_run(autopilot_id, run))
            seen[key] = marker
            emitted += 1
    save_checkpoint(seen)
    return emitted


def main() -> None:
    print(f"Watching Multica Autopilot runs; interval={INTERVAL}s; count={len(AUTOPILOT_IDS)}", flush=True)
    while True:
        try:
            print(f"Published {poll_once()} Multica completion event(s)", flush=True)
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
            print(f"poll failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
