"""
Pytest fixtures for RuntimeDispatcher tests.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from dataclasses import dataclass, field

from core.agent_adapter import (
    AgentAdapter,
    AgentRequest,
    AgentResponse,
    AgentManifest,
    AgentCapabilities,
    AgentHealth,
    AgentRegistry,
)
from core.job import Job, JobPriority, JobRequirements, JobStatus
from core.runtime_dispatcher import RuntimeDispatcher


class MockAgentAdapter(AgentAdapter):
    """Mock agent adapter for testing."""

    def __init__(
        self,
        agent_id: str = "mock_agent",
        is_connected: bool = True,
        supports_streaming: bool = False,
    ):
        manifest = AgentManifest(
            id=agent_id,
            name=f"Mock Agent {agent_id}",
            capabilities=AgentCapabilities(
                supports_streaming=supports_streaming,
                supports_function_calling=False,
                supports_vision=False,
                max_context_length=100000,
            ),
        )
        super().__init__(enabled=True)
        self.manifest = manifest
        self._is_connected = is_connected

    async def _connect_impl(self) -> bool:
        self.health.state = "connected"
        return True

    async def _disconnect_impl(self):
        self.health.state = "disconnected"

    async def _send_request_impl(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            request_id=request.id,
            content=f"response to: {request.prompt}",
            done=True,
        )

    async def _send_request_streaming_impl(self, request: AgentRequest):
        yield AgentResponse(
            request_id=request.id,
            delta="streaming ",
            done=False,
        )
        yield AgentResponse(
            request_id=request.id,
            content="response",
            done=True,
            done_reason="stream_complete",
        )

    @property
    def is_connected(self) -> bool:
        return self._is_connected


@pytest.fixture
def mock_agent():
    """Create a mock agent adapter."""
    agent = MockAgentAdapter()
    agent.health.state = "connected"
    agent.health.total_requests = 0
    agent.health.total_errors = 0
    return agent


@pytest.fixture
def mock_agents():
    """Create multiple mock agents with different loads."""
    agents = []

    for i in range(3):
        agent = MockAgentAdapter(agent_id=f"agent_{i}")
        agent.health.state = "connected"
        # Different load levels
        agent.health.total_requests = i * 10
        agent.health.total_errors = 0
        agents.append(agent)

    return agents


@pytest.fixture
def agent_registry(mock_agents):
    """Create an agent registry with mock agents."""
    registry = AgentRegistry()
    for agent in mock_agents:
        registry.register(agent)
    return registry


@pytest.fixture
def dispatcher(agent_registry):
    """Create a RuntimeDispatcher with mock registry."""
    return RuntimeDispatcher(
        agent_registry=agent_registry,
        max_concurrent_jobs=10,
        default_timeout_seconds=60,
    )


@pytest.fixture
def sample_request():
    """Create a sample AgentRequest."""
    return AgentRequest(
        prompt="Hello, agent!",
        system="You are a helpful assistant.",
    )


@pytest.fixture
def sample_job_requirements():
    """Create sample job requirements."""
    return JobRequirements(
        required_capabilities=[],
        required_agents=[],
        excluded_agents=[],
        max_context_length=0,
    )
