"""Disabled example connector; replace this with a real trusted adapter."""
from __future__ import annotations

import os
import time


if __name__ == "__main__":
    print(f"connector={os.environ['PULSERELAY_CONNECTOR_ID']} state={os.environ['PULSERELAY_STATE_DIR']}", flush=True)
    while True:
        time.sleep(60)
