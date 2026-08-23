"""A durable GitHub Releases polling connector.

Copy this directory to the mounted ``connectors/`` volume, set the repository
in connector.yaml, then set ``enabled: true``. The connector uses only Python's
standard library, stores its cursor below PULSERELAY_STATE_DIR, and posts
normalized EventEnvelope JSON to PULSERELAY_INGEST_URL.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
INGEST_URL = os.environ.get("PULSERELAY_INGEST_URL", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
INTERVAL = max(5, int(os.environ.get("POLL_INTERVAL_SECONDS", "60")))
INITIAL_SYNC_LIMIT = max(1, int(os.environ.get("INITIAL_SYNC_LIMIT", "1")))
STATE_DIR = Path(os.environ.get("PULSERELAY_STATE_DIR", ".state"))
STATE_FILE = STATE_DIR / "checkpoint.json"


def load_checkpoint() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen_ids": []}


def save_checkpoint(checkpoint: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def fetch_releases() -> list[dict]:
    if not REPOSITORY or not INGEST_URL:
        raise RuntimeError("GITHUB_REPOSITORY and PULSERELAY_INGEST_URL are required")
    url = f"{API_URL}/repos/{quote(REPOSITORY, safe='/')}/releases?per_page=100"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "PulseRelay-my-source"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("GitHub releases response was not a list")
    return [item for item in payload if isinstance(item, dict) and item.get("id") and not item.get("draft")]


def normalize_release(release: dict) -> dict:
    release_id = str(release["id"])
    published_at = release.get("published_at") or release.get("created_at") or datetime.now(timezone.utc).isoformat()
    return {
        "id": f"github:release:{REPOSITORY}:{release_id}",
        "source": {"type": "github", "id": REPOSITORY, "name": "GitHub"},
        "sender": {
            "id": str((release.get("author") or {}).get("login", "github")),
            "name": str((release.get("author") or {}).get("login", "GitHub")),
            "role": "system",
            "trust_level": "system",
        },
        "event": {
            "type": "github.release.published",
            "action": "published",
            "timestamp": published_at,
            "dedupe_key": f"github:release:{REPOSITORY}:{release_id}:{release.get('updated_at', published_at)}",
        },
        "content": {
            "title": str(release.get("name") or release.get("tag_name") or "GitHub release"),
            "text": str(release.get("body") or ""),
            "raw": {"release_id": release_id, "tag_name": release.get("tag_name"), "url": release.get("html_url")},
        },
        "context": {"repo": REPOSITORY, "url": release.get("html_url", "")},
        "routing": {"priority": "normal", "labels": ["github", "release"]},
    }


def publish(event: dict) -> None:
    body = json.dumps(event, ensure_ascii=False).encode("utf-8")
    request = Request(INGEST_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=30) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"PulseRelay rejected event with HTTP {response.status}")


def poll_once() -> int:
    checkpoint = load_checkpoint()
    seen = {str(item) for item in checkpoint.get("seen_ids", [])}
    releases = fetch_releases()
    unseen = [item for item in releases if str(item["id"]) not in seen]
    if not checkpoint.get("seen_ids"):
        unseen = unseen[:INITIAL_SYNC_LIMIT]
    # GitHub returns newest first; publish oldest first when catching up.
    unseen.sort(key=lambda item: item.get("published_at") or item.get("created_at") or "")
    for release in unseen:
        publish(normalize_release(release))
        seen.add(str(release["id"]))
        checkpoint["seen_ids"] = list(seen)[-500:]
        checkpoint["last_success_at"] = datetime.now(timezone.utc).isoformat()
        checkpoint["last_release_id"] = str(release["id"])
        save_checkpoint(checkpoint)
    return len(unseen)


def main() -> None:
    print(f"Watching GitHub releases for {REPOSITORY}; interval={INTERVAL}s", flush=True)
    while True:
        try:
            print(f"Published {poll_once()} new release event(s)", flush=True)
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
            # Keep the checkpoint unchanged. Supervisor restarts the process if
            # it exits; transient failures are also retried by this loop.
            print(f"poll failed: {exc}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
