"""Unified PulseRelay ASGI application.

REST/Webhook data plane and MCP control plane share port 8000. MCP is mounted
at /mcp. PulseRelay also owns its API-key/OAuth credential management endpoints
so the existing Web UI does not proxy those resources to another product.
"""
from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request

from api import create_app
from core.mcp_auth import ALL_SCOPES, get_auth_store
from mcp_control import MCPControlPlaneAuth, mcp


data_app = create_app()
auth_store = get_auth_store()
mcp_protocol_app = mcp.streamable_http_app(
    streamable_http_path="/", json_response=True,
    host=os.getenv("PULSERELAY_MCP_HOST", "0.0.0.0"),
)
mcp_app = MCPControlPlaneAuth(mcp_protocol_app)


def _admin(request: Request) -> None:
    expected = os.getenv("PULSERELAY_ADMIN_TOKEN", "").strip()
    supplied = request.headers.get("authorization", "")
    scheme, _, token = supplied.partition(" ")
    if not expected:
        raise HTTPException(503, "PulseRelay admin authentication is not configured")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token.strip(), expected):
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})


def _payload_scopes(payload: dict):
    return payload.get("scopes", payload.get("scope", []))


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with data_app.router.lifespan_context(data_app):
        async with mcp_protocol_app.router.lifespan_context(mcp_protocol_app):
            yield


app = FastAPI(title="PulseRelay", version="1.2", lifespan=lifespan)


@app.get("/v1/mcp/scopes")
async def mcp_scopes(request: Request):
    _admin(request)
    return {"scopes": sorted(ALL_SCOPES)}


@app.get("/v1/mcp/api-keys")
async def list_mcp_api_keys(request: Request):
    _admin(request)
    return {"api_keys": auth_store.list_api_keys()}


@app.post("/v1/mcp/api-keys")
async def create_mcp_api_key(payload: dict, request: Request):
    _admin(request)
    try:
        item = auth_store.create_api_key(str(payload.get("name") or "API Key"), _payload_scopes(payload))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"api_key": item}


@app.post("/v1/mcp/api-keys/revoke")
async def revoke_mcp_api_key(payload: dict, request: Request):
    _admin(request)
    key_id = str(payload.get("id") or payload.get("key_id") or "")
    if not key_id or not auth_store.revoke_api_key(key_id):
        raise HTTPException(404, "API key not found")
    return {"revoked": True, "id": key_id}


@app.get("/v1/mcp/oauth-clients")
async def list_mcp_oauth_clients(request: Request):
    _admin(request)
    return {"oauth_clients": auth_store.list_oauth_clients()}


@app.post("/v1/mcp/oauth-clients")
async def create_mcp_oauth_client(payload: dict, request: Request):
    _admin(request)
    try:
        item = auth_store.create_oauth_client(str(payload.get("name") or "OAuth Client"), _payload_scopes(payload))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"oauth_client": item}


@app.post("/v1/mcp/oauth-clients/revoke")
async def revoke_mcp_oauth_client(payload: dict, request: Request):
    _admin(request)
    client_id = str(payload.get("client_id") or payload.get("id") or "")
    if not client_id or not auth_store.revoke_oauth_client(client_id):
        raise HTTPException(404, "OAuth client not found")
    return {"revoked": True, "client_id": client_id}


async def _issue_token(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
    else:
        form = parse_qs((await request.body()).decode("utf-8"))
        payload = {key: values[-1] for key, values in form.items()}
    if str(payload.get("grant_type") or "client_credentials") != "client_credentials":
        raise HTTPException(400, "unsupported_grant_type")
    try:
        token = auth_store.issue_client_credentials_token(
            str(payload.get("client_id") or ""), str(payload.get("client_secret") or ""),
            payload.get("scope") or payload.get("scopes"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if token is None:
        raise HTTPException(401, "invalid_client")
    return token


@app.post("/oauth/token")
async def oauth_token(request: Request):
    return await _issue_token(request)


@app.post("/token")
async def oauth_token_compat(request: Request):
    return await _issue_token(request)


app.mount("/mcp", mcp_app, name="mcp")
app.mount("/", data_app, name="pulserelay")
