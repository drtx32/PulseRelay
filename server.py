"""Unified PulseRelay ASGI application.

The data-plane HTTP API/Webhooks and the remote MCP control plane share one
process, one SQLite-backed service layer, and one public port. MCP is mounted at
`/mcp`; it configures the graph but never carries outbound event delivery.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api import create_app
from mcp_control import BearerControlPlaneAuth, mcp


# Keep the existing PulseRelay FastAPI application intact. It owns the webhook
# data plane, existing REST API, browser assets, and event stream.
data_app = create_app()

# Mount MCP underneath the main application. Because the outer app removes the
# `/mcp` mount prefix before delegation, the MCP sub-app serves its protocol at
# `/` internally. Externally clients use the single canonical URL `:8000/mcp`.
mcp_protocol_app = mcp.streamable_http_app(
    streamable_http_path="/",
    json_response=True,
    host=os.getenv("PULSERELAY_MCP_HOST", "0.0.0.0"),
)
mcp_app = BearerControlPlaneAuth(mcp_protocol_app)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Mounted ASGI applications do not automatically receive independent
    # lifespan events from every server stack. Explicitly enter both contexts
    # so the existing PulseRelay startup/shutdown hooks and the MCP session
    # manager are active together.
    async with data_app.router.lifespan_context(data_app):
        async with mcp_protocol_app.router.lifespan_context(mcp_protocol_app):
            yield


app = FastAPI(title="PulseRelay", version="1.1", lifespan=lifespan)

# More-specific mount first. `/` remains the existing application and therefore
# preserves every current REST/Webhook/UI path without migration.
app.mount("/mcp", mcp_app, name="mcp")
app.mount("/", data_app, name="pulserelay")
