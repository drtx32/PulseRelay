"""Run the durable webhook delivery queue as a long-lived process."""
from __future__ import annotations

import asyncio
import os

from core.persistence import SQLitePhase9Store
from core.relay import DurableRelay


async def run() -> None:
    store = SQLitePhase9Store(os.getenv("PULSERELAY_DB_PATH", "data/pulserelay.db"))
    relay = DurableRelay(store, worker_id=os.getenv("PULSERELAY_WORKER_ID", "docker-worker"))
    interval = max(0.1, float(os.getenv("PULSERELAY_WORKER_INTERVAL", "1")))
    try:
        while True:
            relay.flush()
            result = await relay.process_one()
            if result is None:
                await asyncio.sleep(interval)
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(run())
