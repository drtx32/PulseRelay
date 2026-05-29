"""
Agent adapter abstractions.

An agent adapter is responsible for:
- connecting to AI agent runtimes (Claude, OpenAI, local agents, etc.)
- maintaining the agent connection lifecycle (keep-alive, reconnect)
- sending prompts/messages to agents
- receiving and normalizing responses
- streaming response handling
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, AsyncIterator
from uuid import uuid4

AgentState = Literal[
    "created",
    "connecting",
    "connected",
    "streaming",
    "idle",
    "disconnected",
    "error",
]


@dataclass
class AgentCapabilities:
    """Capabilities exposed by an agent adapter."""

    supports_streaming: bool = False
    supports_function_calling: bool = False
    supports_json_mode: bool = False
    supports_vision: bool = False
    supports_context_caching: bool = False
    max_context_length: int = 0


@dataclass
class AgentHealth:
    """Runtime health metadata for an agent adapter."""

    state: AgentState = "created"
    last_request_at: str = ""
    last_response_at: str = ""
    last_error: str = ""
    reconnect_count: int = 0
    total_requests: int = 0
    total_errors: int = 0


@dataclass
class AgentManifest:
    """Static metadata describing an agent adapter."""

    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    tags: list[str] = field(default_factory=list)


@dataclass
class AgentRequest:
    """A request sent to an agent."""

    id: str = field(default_factory=lambda: f"req_{uuid4().hex}")
    prompt: str = ""
    system: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)  # conversation history
    context: dict[str, Any] = field(default_factory=dict)  # source, sender, routing info

    temperature: float = 0.7
    max_tokens: int = 4096
    streaming: bool = False

    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResponse:
    """A response received from an agent."""

    id: str = field(default_factory=lambda: f"resp_{uuid4().hex}")
    request_id: str = ""
    content: str = ""
    done: bool = False
    error: str = ""

    # For streaming responses
    delta: str = ""
    done_reason: str = ""

    usage: dict[str, Any] = field(default_factory=dict)  # token usage stats
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentAdapter(ABC):
    """
    Base class for all agent runtime adapters.

    Agent adapters bridge PulseRelay to AI agent runtimes. They handle:
    - Connection management (connect, keep-alive, reconnect)
    - Request/response protocol
    - Response normalization
    - Error handling and recovery
    """

    manifest: AgentManifest

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.health = AgentHealth()

    @property
    def agent_id(self) -> str:
        return self.manifest.id

    @property
    def is_connected(self) -> bool:
        return self.health.state in {"connected", "streaming", "idle"}

    async def emit_response(self, response: AgentResponse):
        """Called by subclasses when a response is received."""
        self.health.last_response_at = datetime.now(timezone.utc).isoformat()
        self.health.total_requests += 1

    async def emit_error(self, error: str):
        """Called by subclasses when an error occurs."""
        self.health.last_error = error
        self.health.total_errors += 1
        self.health.state = "error"

    async def connect(self) -> bool:
        """Establish connection to the agent runtime."""

        self.health.state = "connecting"

        try:
            success = await self._connect_impl()
            if success:
                self.health.state = "connected"
                return True
            else:
                self.health.state = "error"
                return False
        except Exception as exc:
            self.health.state = "error"
            self.health.last_error = str(exc)
            return False

    async def disconnect(self):
        """Close connection to the agent runtime."""

        self.health.state = "disconnected"
        await self._disconnect_impl()

    async def send_request(self, request: AgentRequest) -> AgentResponse:
        """Send a request to the agent and wait for response."""

        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                return AgentResponse(
                    request_id=request.id,
                    error="failed to connect to agent",
                    done=True,
                )

        self.health.last_request_at = datetime.now(timezone.utc).isoformat()

        try:
            response = await self._send_request_impl(request)
            await self.emit_response(response)
            return response
        except Exception as exc:
            await self.emit_error(str(exc))
            return AgentResponse(
                request_id=request.id,
                error=str(exc),
                done=True,
            )

    async def send_request_streaming(self, request: AgentRequest) -> AsyncIterator[AgentResponse]:
        """Send a request and yield streaming responses."""

        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                yield AgentResponse(
                    request_id=request.id,
                    error="failed to connect to agent",
                    done=True,
                )
                return

        self.health.last_request_at = datetime.now(timezone.utc).isoformat()
        self.health.state = "streaming"

        try:
            async for response in self._send_request_streaming_impl(request):
                await self.emit_response(response)
                yield response
        except Exception as exc:
            await self.emit_error(str(exc))
            yield AgentResponse(
                request_id=request.id,
                error=str(exc),
                done=True,
            )
        finally:
            self.health.state = "connected"

    # --- Abstract methods to implement ---

    @abstractmethod
    async def _connect_impl(self) -> bool:
        """Actual connection logic. Return True on success."""
        raise NotImplementedError

    @abstractmethod
    async def _disconnect_impl(self):
        """Actual disconnection logic."""
        raise NotImplementedError

    @abstractmethod
    async def _send_request_impl(self, request: AgentRequest) -> AgentResponse:
        """Send request and wait for full response."""
        raise NotImplementedError

    @abstractmethod
    async def _send_request_streaming_impl(self, request: AgentRequest) -> AsyncIterator[AgentResponse]:
        """Send request and yield streaming responses."""
        raise NotImplementedError


class AgentRegistry:
    """Runtime registry for agent adapters."""

    def __init__(self):
        self._agents: dict[str, AgentAdapter] = {}

    def register(self, agent: AgentAdapter):
        self._agents[agent.agent_id] = agent

    def unregister(self, agent_id: str):
        """Remove an agent from the registry."""
        self._agents.pop(agent_id, None)

    def get(self, agent_id: str) -> AgentAdapter | None:
        return self._agents.get(agent_id)

    def list(self) -> list[AgentAdapter]:
        return list(self._agents.values())

    def list_by_capability(self, capability: str) -> list[AgentAdapter]:
        """List agents that support a specific capability."""
        agents = []
        for agent in self._agents.values():
            if capability == "streaming" and agent.manifest.capabilities.supports_streaming:
                agents.append(agent)
            elif capability == "function_calling" and agent.manifest.capabilities.supports_function_calling:
                agents.append(agent)
            elif capability == "vision" and agent.manifest.capabilities.supports_vision:
                agents.append(agent)
        return agents

    def health_snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            agent.agent_id: {
                "state": agent.health.state,
                "is_connected": agent.is_connected,
                "last_request_at": agent.health.last_request_at,
                "last_response_at": agent.health.last_response_at,
                "last_error": agent.health.last_error,
                "reconnect_count": agent.health.reconnect_count,
                "total_requests": agent.health.total_requests,
                "total_errors": agent.health.total_errors,
            }
            for agent in self._agents.values()
        }


# ============================================================================
# OpenClaw Adapter Implementation
# ============================================================================

import json
import time
import websockets
from typing import AsyncIterator


class OpenClawAdapter(AgentAdapter):
    """
    AgentAdapter implementation for OpenClaw WebSocket runtime.

    OpenClaw is a local agent runtime that exposes a WebSocket API.
    This adapter handles connection management and request/response
    protocol for communicating with OpenClaw.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18800,
        path: str = "/ws",
        sender_id: str = "pulse_relay",
        sender_name: str = "PulseRelay",
        token: str = "",
        enabled: bool = True,
    ):
        # Build manifest
        manifest = AgentManifest(
            id="openclaw",
            name="OpenClaw Agent",
            version="1.0.0",
            description="OpenClaw WebSocket agent runtime",
            capabilities=AgentCapabilities(
                supports_streaming=True,
                supports_function_calling=False,
                supports_json_mode=False,
                supports_vision=False,
                supports_context_caching=False,
                max_context_length=0,  # Determined at runtime
            ),
            tags=["websocket", "local", "openclaw"],
        )

        super().__init__(enabled=enabled)
        self.manifest = manifest

        # Connection settings
        self.host = host
        self.port = port
        self.path = path
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.token = token

        # Connection state
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._connect_lock = asyncio.Lock()

    def _build_url(self) -> str:
        """Build WebSocket URL with query parameters."""
        url = f"ws://{self.host}:{self.port}{self.path}?senderId={self.sender_id}&senderName={self.sender_name}"
        if self.token:
            url += f"&token={self.token}"
        return url

    # === AgentAdapter Implementation ===

    async def _connect_impl(self) -> bool:
        """Establish WebSocket connection to OpenClaw."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        try:
            url = self._build_url()
            self._ws = await websockets.connect(url)
            logger.info(f"Connected to OpenClaw at {url}")
            return True
        except Exception as exc:
            logger.error(f"Failed to connect to OpenClaw: {exc}")
            self.health.last_error = str(exc)
            return False

    async def _disconnect_impl(self):
        """Close WebSocket connection."""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
            logger.info("Disconnected from OpenClaw")

    async def _send_request_impl(self, request: AgentRequest) -> AgentResponse:
        """Send a request and wait for full response."""
        if not self._ws:
            connected = await self.connect()
            if not connected:
                return AgentResponse(
                    request_id=request.id,
                    error="failed to connect to OpenClaw",
                    done=True,
                )

        # Build message
        message_id = f"req_{int(time.time() * 1000)}"
        msg = {
            "type": "chat.send",
            "messageId": message_id,
            "content": request.prompt,
            "senderId": self.sender_id,
            "senderName": self.sender_name,
        }

        try:
            await self._ws.send(json.dumps(msg))
            logger.info(f"Sent to OpenClaw: {request.prompt[:100]}")

            # Collect response
            full_content = ""
            async for raw_message in self._ws:
                msg_data = json.loads(raw_message)
                msg_type = msg_data.get("type", "")

                if msg_type == "chat.stream":
                    full_content = msg_data.get("content", "") or ""

                elif msg_type == "chat.response":
                    if msg_data.get("done"):
                        return AgentResponse(
                            request_id=request.id,
                            content=full_content if full_content else msg_data.get("content", ""),
                            done=True,
                            done_reason="complete",
                        )

                elif msg_type == "chat.error":
                    return AgentResponse(
                        request_id=request.id,
                        error=msg_data.get("error", "Unknown error"),
                        done=True,
                        done_reason="error",
                    )

            # Connection closed without response
            return AgentResponse(
                request_id=request.id,
                content=full_content,
                done=True,
                done_reason="connection_closed",
            )

        except Exception as exc:
            logger.error(f"OpenClaw request failed: {exc}")
            return AgentResponse(
                request_id=request.id,
                error=str(exc),
                done=True,
            )

    async def _send_request_streaming_impl(self, request: AgentRequest) -> AsyncIterator[AgentResponse]:
        """Send a request and yield streaming responses."""
        if not self._ws:
            connected = await self.connect()
            if not connected:
                yield AgentResponse(
                    request_id=request.id,
                    error="failed to connect to OpenClaw",
                    done=True,
                )
                return

        message_id = f"req_{int(time.time() * 1000)}"
        msg = {
            "type": "chat.send",
            "messageId": message_id,
            "content": request.prompt,
            "senderId": self.sender_id,
            "senderName": self.sender_name,
        }

        try:
            await self._ws.send(json.dumps(msg))
            logger.info(f"Streaming to OpenClaw: {request.prompt[:100]}")

            async for raw_message in self._ws:
                msg_data = json.loads(raw_message)
                msg_type = msg_data.get("type", "")

                if msg_type == "chat.stream":
                    delta = msg_data.get("content", "") or ""
                    if delta:
                        yield AgentResponse(
                            request_id=request.id,
                            delta=delta,
                            done=False,
                        )

                elif msg_type == "chat.response":
                    if msg_data.get("done"):
                        yield AgentResponse(
                            request_id=request.id,
                            content=msg_data.get("content", ""),
                            done=True,
                            done_reason="complete",
                        )
                        return

                elif msg_type == "chat.error":
                    yield AgentResponse(
                        request_id=request.id,
                        error=msg_data.get("error", "Unknown error"),
                        done=True,
                        done_reason="error",
                    )
                    return

        except Exception as exc:
            logger.error(f"OpenClaw streaming failed: {exc}")
            yield AgentResponse(
                request_id=request.id,
                error=str(exc),
                done=True,
            )


# ============================================================================
# OpenAI Adapter (Stub for future implementation)
# ============================================================================


class OpenAIAdapter(AgentAdapter):
    """
    AgentAdapter implementation for OpenAI API.

    This is a stub implementation that can be completed when
    OpenAI API integration is needed.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "gpt-4o",
        api_url: str = "https://api.openai.com",
        enabled: bool = True,
    ):
        manifest = AgentManifest(
            id="openai",
            name="OpenAI Agent",
            version="1.0.0",
            description="OpenAI GPT agent",
            capabilities=AgentCapabilities(
                supports_streaming=True,
                supports_function_calling=True,
                supports_json_mode=True,
                supports_vision=True,
                supports_context_caching=False,
                max_context_length=128000,
            ),
            tags=["openai", "api", "gpt"],
        )

        super().__init__(enabled=enabled)
        self.manifest = manifest
        self.api_key = api_key
        self.model = model
        self.api_url = api_url

    async def _connect_impl(self) -> bool:
        """OpenAI API doesn't require persistent connection."""
        self.health.state = "connected"
        return True

    async def _disconnect_impl(self):
        """OpenAI API doesn't require disconnection."""
        pass

    async def _send_request_impl(self, request: AgentRequest) -> AgentResponse:
        """Send request to OpenAI API."""
        # Stub - would use openai SDK
        return AgentResponse(
            request_id=request.id,
            error="OpenAI adapter not yet implemented",
            done=True,
        )

    async def _send_request_streaming_impl(self, request: AgentRequest) -> AsyncIterator[AgentResponse]:
        """Stream from OpenAI API."""
        yield AgentResponse(
            request_id=request.id,
            error="OpenAI adapter not yet implemented",
            done=True,
        )
