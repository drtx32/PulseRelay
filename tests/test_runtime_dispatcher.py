"""
Unit tests for RuntimeDispatcher.

Tests job submission, agent selection, job execution, timeout handling,
job retrieval, and job cancellation.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.agent_adapter import (
    AgentRequest,
    AgentResponse,
    AgentManifest,
    AgentCapabilities,
    AgentRegistry,
)
from core.job import Job, JobPriority, JobRequirements, JobStatus
from core.runtime_dispatcher import RuntimeDispatcher

from tests.conftest import MockAgentAdapter


class TestSubmitJob:
    """Tests for submit_job functionality."""

    @pytest.mark.asyncio
    async def test_submit_job_creates_job(self, dispatcher, sample_request):
        """submit_job() should create a Job and return it."""
        job = await dispatcher.submit_job(
            request=sample_request,
            session_id="test_session",
            priority=JobPriority.NORMAL,
        )

        assert job is not None
        assert isinstance(job, Job)
        assert job.request == sample_request
        assert job.session_id == "test_session"
        assert job.priority == JobPriority.NORMAL
        assert job.status == JobStatus.PENDING
        assert job.id in dispatcher._jobs

    @pytest.mark.asyncio
    async def test_submit_job_enqueues_job(self, dispatcher, sample_request):
        """submit_job() should add Job to priority queue."""
        job = await dispatcher.submit_job(
            request=sample_request,
            priority=JobPriority.HIGH,
        )

        # Verify job is in the high priority queue
        queue = dispatcher._queues[JobPriority.HIGH]
        assert queue.qsize() > 0

        # Check that job was actually queued by dequeuing
        _, queued_job = queue.get_nowait()
        assert queued_job.id == job.id


class TestSelectAgent:
    """Tests for _select_agent functionality."""

    @pytest.mark.asyncio
    async def test_select_agent_prefers_less_loaded(self, mock_agents):
        """_select_agent should select the agent with lowest load."""
        # Create agents with different loads
        agent_low = MockAgentAdapter(agent_id="agent_low")
        agent_low.health.total_requests = 5
        agent_low.health.total_errors = 0

        agent_high = MockAgentAdapter(agent_id="agent_high")
        agent_high.health.total_requests = 100
        agent_high.health.total_errors = 5

        registry = AgentRegistry()
        registry.register(agent_low)
        registry.register(agent_high)

        dispatcher = RuntimeDispatcher(agent_registry=registry)

        job = Job(request=AgentRequest(prompt="test"))

        selected = dispatcher._select_agent(job)

        assert selected is not None
        assert selected.agent_id == "agent_low"

    @pytest.mark.asyncio
    async def test_select_agent_skips_disconnected(self):
        """_select_agent should skip disconnected agents."""
        # Create one connected and one disconnected agent
        agent_connected = MockAgentAdapter(agent_id="agent_connected", is_connected=True)
        agent_connected.health.state = "connected"

        agent_disconnected = MockAgentAdapter(agent_id="agent_disconnected", is_connected=False)
        agent_disconnected.health.state = "disconnected"

        registry = AgentRegistry()
        registry.register(agent_connected)
        registry.register(agent_disconnected)

        dispatcher = RuntimeDispatcher(agent_registry=registry)

        job = Job(request=AgentRequest(prompt="test"))

        selected = dispatcher._select_agent(job)

        assert selected is not None
        assert selected.agent_id == "agent_connected"
        assert selected._is_connected is True


class TestRunJob:
    """Tests for _run_job functionality."""

    @pytest.mark.asyncio
    async def test_run_job_completes_successfully(self, dispatcher):
        """_run_job should execute job and set status to COMPLETED."""
        # Create a real agent mock
        agent = MockAgentAdapter(agent_id="test_agent")
        agent.health.state = "connected"
        agent.health.total_requests = 0
        agent.health.total_errors = 0

        # Mock send_request
        async def mock_send(request):
            return AgentResponse(
                request_id=request.id,
                content="job completed successfully",
                done=True,
            )
        agent.send_request = AsyncMock(side_effect=mock_send)

        registry = AgentRegistry()
        registry.register(agent)

        dispatcher.agent_registry = registry

        job = Job(
            request=AgentRequest(prompt="test job"),
            timeout_seconds=30,
        )

        await dispatcher._run_job(job)

        assert job.status == JobStatus.COMPLETED
        assert job.response is not None
        assert job.response.content == "job completed successfully"

    @pytest.mark.asyncio
    async def test_run_job_handles_timeout(self, dispatcher):
        """Timeout should mark job status as TIMEOUT."""
        agent = MockAgentAdapter(agent_id="slow_agent")
        agent.health.state = "connected"

        # Mock send_request to delay longer than timeout
        async def slow_send(request):
            await asyncio.sleep(10)  # Longer than timeout
            return AgentResponse(request_id=request.id, content="done", done=True)
        agent.send_request = AsyncMock(side_effect=slow_send)

        registry = AgentRegistry()
        registry.register(agent)
        dispatcher.agent_registry = registry

        job = Job(
            request=AgentRequest(prompt="slow job"),
            timeout_seconds=0.1,  # Very short timeout
            max_retries=0,  # Disable retries to verify timeout state
        )

        await dispatcher._run_job(job)

        assert job.status == JobStatus.TIMEOUT
        assert "timed out" in job.error.lower()


class TestGetJob:
    """Tests for get_job functionality."""

    @pytest.mark.asyncio
    async def test_get_job_returns_job(self, dispatcher, sample_request):
        """get_job should return the correct Job by ID."""
        job = await dispatcher.submit_job(request=sample_request)

        retrieved = dispatcher.get_job(job.id)

        assert retrieved is not None
        assert retrieved.id == job.id
        assert retrieved.request == sample_request

    @pytest.mark.asyncio
    async def test_get_job_returns_none_for_missing(self, dispatcher):
        """get_job should return None for non-existent job ID."""
        result = dispatcher.get_job("non_existent_id")
        assert result is None


class TestCancelJob:
    """Tests for cancel_job functionality."""

    @pytest.mark.asyncio
    async def test_cancel_job(self, dispatcher, sample_request):
        """cancel_job should set Job status to CANCELLED."""
        job = await dispatcher.submit_job(request=sample_request)

        result = dispatcher.cancel_job(job.id)

        assert result is True
        assert job.status == JobStatus.CANCELLED
        assert job.error == "cancelled by user"

    @pytest.mark.asyncio
    async def test_cancel_job_returns_false_for_missing(self, dispatcher):
        """cancel_job should return False for non-existent job."""
        result = dispatcher.cancel_job("non_existent_id")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_job_returns_false_for_terminal(self, dispatcher, sample_request):
        """cancel_job should return False for already completed job."""
        job = await dispatcher.submit_job(request=sample_request)
        job.status = JobStatus.COMPLETED

        result = dispatcher.cancel_job(job.id)
        assert result is False
