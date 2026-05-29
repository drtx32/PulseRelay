"""
RuntimeDispatcher - Job/task distribution for PulseRelay Phase 6.

RuntimeDispatcher is responsible for:
- Accepting async jobs/tasks from MessageRelay or external sources
- Queuing and scheduling jobs based on priority/affinity
- Distributing jobs to available AgentAdapter instances
- Managing job lifecycle (pending, running, completed, failed)
- Handling agent affinity (certain jobs go to certain agents)
- Load balancing across multiple agent instances
- Job cancellation and timeout handling
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from core.agent_adapter import AgentAdapter, AgentRequest, AgentResponse
from core.job import (
    Job,
    JobPriority,
    JobStatus,
    JobRequirements,
    DispatchResult,
)

logger = logging.getLogger(__name__)


class RuntimeDispatcher:
    """
    Job scheduler and dispatcher.

    RuntimeDispatcher manages the lifecycle of jobs from submission to completion:

    1. Submit job -> Job created with PENDING status
    2. Schedule job -> Job queued based on priority
    3. Dispatch job -> Job assigned to available agent
    4. Execute job -> Agent processes request
    5. Complete job -> Response captured, callbacks invoked

    Features:
    - Priority queuing (urgent jobs processed first)
    - Agent affinity (jobs can target specific agents)
    - Capability matching (jobs require certain agent capabilities)
    - Load balancing (spread work across agent instances)
    - Timeout handling
    - Automatic retry with backoff
    """

    def __init__(
        self,
        agent_registry: AgentRegistry | None = None,
        max_concurrent_jobs: int = 10,
        default_timeout_seconds: float = 300,
    ):
        self.agent_registry = agent_registry

        # Job queues by priority
        self._queues: dict[JobPriority, asyncio.PriorityQueue] = {
            JobPriority.LOW: asyncio.PriorityQueue(maxsize=1000),
            JobPriority.NORMAL: asyncio.PriorityQueue(maxsize=500),
            JobPriority.HIGH: asyncio.PriorityQueue(maxsize=200),
            JobPriority.URGENT: asyncio.PriorityQueue(maxsize=100),
        }

        # All jobs by ID
        self._jobs: dict[str, Job] = {}

        # Jobs currently running, keyed by agent_id
        self._running_jobs: dict[str, Job] = {}

        # Callbacks
        self.on_job_complete: Callable[[Job], Awaitable[None]] | None = None
        self.on_job_failed: Callable[[Job], Awaitable[None]] | None = None
        self.on_job_queued: Callable[[Job], Awaitable[None]] | None = None

        # Limits
        self.max_concurrent_jobs = max_concurrent_jobs
        self.default_timeout_seconds = default_timeout_seconds

        # State
        self._running = False
        self._dispatcher_task: asyncio.Task | None = None

    # === Lifecycle ===

    async def start(self):
        """Start the dispatcher scheduler loop."""

        if self._running:
            return

        self._running = True
        self._dispatcher_task = asyncio.create_task(self._scheduler_loop())
        logger.info("RuntimeDispatcher started")

    async def stop(self):
        """Stop the dispatcher and cancel pending jobs."""

        self._running = False

        if self._dispatcher_task:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None

        # Cancel running jobs
        for job in self._running_jobs.values():
            job.status = JobStatus.CANCELLED
            job.error = "dispatcher stopped"

        logger.info("RuntimeDispatcher stopped")

    async def _scheduler_loop(self):
        """Main scheduler loop - processes job queues."""

        while self._running:
            try:
                # Check for available capacity
                if len(self._running_jobs) < self.max_concurrent_jobs:
                    # Try to dispatch next job
                    job = await self._dequeue_job()
                    if job:
                        asyncio.create_task(self._run_job(job))

                # Check for timed out jobs
                await self._check_timeouts()

                # Sleep before next iteration
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                await asyncio.sleep(1)

    # === Job Submission ===

    async def submit_job(
        self,
        request: AgentRequest,
        session_id: str = "",
        priority: JobPriority = JobPriority.NORMAL,
        requirements: JobRequirements | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Job:
        """
        Submit a new job for execution.

        Returns the created Job object.
        """

        job = Job(
            request=request,
            session_id=session_id,
            priority=priority,
            requirements=requirements or JobRequirements(),
            timeout_seconds=timeout_seconds or self.default_timeout_seconds,
            metadata=metadata or {},
        )

        self._jobs[job.id] = job

        # Enqueue the job for the scheduler to pick up
        await self._enqueue_job(job)

        if self.on_job_queued:
            asyncio.create_task(self.on_job_queued(job))

        return job

    async def _dequeue_job(self) -> Job | None:
        """Dequeue the next job based on priority."""

        # Check queues in priority order
        for priority in [JobPriority.URGENT, JobPriority.HIGH, JobPriority.NORMAL, JobPriority.LOW]:
            queue = self._queues[priority]

            try:
                # Non-blocking peek
                if queue.qsize() > 0:
                    _, job = queue.get_nowait()
                    return job
            except asyncio.QueueEmpty:
                continue

        return None

    # === Job Execution ===

    async def dispatch_job(self, job: Job) -> DispatchResult:
        """
        Dispatch a job to an available agent.

        Returns DispatchResult indicating success/failure.
        """

        # Find suitable agent
        agent = self._select_agent(job)

        if not agent:
            # Queue job for later
            job.status = JobStatus.PENDING
            await self._enqueue_job(job)
            return DispatchResult(
                job_id=job.id,
                success=False,
                error="no suitable agent available",
                queued=True,
            )

        # Mark job as scheduled
        job.status = JobStatus.SCHEDULED
        job.scheduled_at = datetime.now(timezone.utc).isoformat()

        # Track running job
        self._running_jobs[agent.agent_id] = job

        # Immediately trigger job execution instead of waiting for scheduler loop
        asyncio.create_task(self._run_job(job))

        return DispatchResult(
            job_id=job.id,
            success=True,
            agent_id=agent.agent_id,
            queued=False,
        )

    async def _run_job(self, job: Job):
        """Execute a job on a selected agent."""

        # Select agent
        agent = self._select_agent(job)
        if not agent:
            job.status = JobStatus.FAILED
            job.error = "no suitable agent available"
            if self.on_job_failed:
                await self.on_job_failed(job)
            return

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc).isoformat()
        self._running_jobs[agent.agent_id] = job

        try:
            # Execute with timeout
            if self._agent_supports_streaming(agent):
                # For streaming, we collect all deltas
                full_content = ""
                async for response in agent.send_request_streaming(job.request):
                    if response.error:
                        job.error = response.error
                        job.status = JobStatus.FAILED
                        break
                    if response.delta:
                        full_content += response.delta

                if job.status != JobStatus.FAILED:
                    job.response = AgentResponse(
                        content=full_content,
                        done=True,
                        done_reason="stream_complete",
                    )
                    job.status = JobStatus.COMPLETED
            else:
                # Non-streaming request
                response = await asyncio.wait_for(
                    agent.send_request(job.request),
                    timeout=job.timeout_seconds,
                )

                if response.error:
                    job.error = response.error
                    job.status = JobStatus.FAILED
                else:
                    job.response = response
                    job.status = JobStatus.COMPLETED

            job.completed_at = datetime.now(timezone.utc).isoformat()

        except asyncio.TimeoutError:
            job.status = JobStatus.TIMEOUT
            job.error = f"job timed out after {job.timeout_seconds} seconds"
            job.completed_at = datetime.now(timezone.utc).isoformat()

        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error = str(exc)
            job.completed_at = datetime.now(timezone.utc).isoformat()

        finally:
            # Remove from running jobs
            self._running_jobs.pop(agent.agent_id, None)

            # Handle completion
            if job.status == JobStatus.COMPLETED:
                if self.on_job_complete:
                    await self.on_job_complete(job)
            else:
                if self.on_job_failed:
                    await self.on_job_failed(job)

                # Retry if possible
                if job.can_retry:
                    job.retry_count += 1
                    await self._enqueue_job(job)

    def _select_agent(self, job: Job) -> AgentAdapter | None:
        """
        Select the best agent for a job based on requirements and affinity.

        Selection criteria:
        1. Required agents (if specified, must be in this list)
        2. Excluded agents (if specified, must NOT be in this list)
        3. Required capabilities (agent must have them)
        4. Availability (connected and not overloaded)
        5. Load balancing (prefer less busy agents)
        """

        if not self.agent_registry:
            return None

        candidates: list[tuple[AgentAdapter, int]] = []  # (agent, load_score)

        for agent in self.agent_registry.list():
            # Skip if not connected
            if not agent.is_connected:
                continue

            # Check required agents
            if job.requirements.required_agents:
                if agent.agent_id not in job.requirements.required_agents:
                    continue

            # Check excluded agents
            if agent.agent_id in job.requirements.excluded_agents:
                continue

            # Check capabilities
            if job.requirements.required_capabilities:
                caps = job.requirements.required_capabilities
                agent_caps = agent.manifest.capabilities

                if "streaming" in caps and not agent_caps.supports_streaming:
                    continue
                if "function_calling" in caps and not agent_caps.supports_function_calling:
                    continue
                if "vision" in caps and not agent_caps.supports_vision:
                    continue

            # Check context length requirement
            if job.requirements.max_context_length > 0:
                if agent.manifest.capabilities.max_context_length < job.requirements.max_context_length:
                    continue

            # Calculate load score (lower is better)
            # Based on recent request count and errors
            load_score = agent.health.total_requests + (agent.health.total_errors * 2)
            candidates.append((agent, load_score))

        if not candidates:
            return None

        # Sort by load score (ascending) and return least loaded
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    def _agent_supports_streaming(self, agent: AgentAdapter) -> bool:
        """Check if agent supports streaming."""
        return agent.manifest.capabilities.supports_streaming

    async def _enqueue_job(self, job: Job):
        """Add job to priority queue."""
        priority = job.priority
        queue = self._queues[priority]

        # Use negative priority so lower number = higher priority (urgency)
        queue.put_nowait((-priority.value, job))

        job.status = JobStatus.PENDING

    async def _check_timeouts(self):
        """Check for and handle job timeouts."""

        now = datetime.now(timezone.utc)

        for agent_id, job in list(self._running_jobs.items()):
            if not job.started_at:
                continue

            started = datetime.fromisoformat(job.started_at)
            elapsed = (now - started).total_seconds()

            if elapsed > job.timeout_seconds:
                logger.warning(f"Job {job.id} timed out after {elapsed:.1f}s")

                # Mark as timed out
                job.status = JobStatus.TIMEOUT
                job.error = f"timed out after {elapsed:.1f} seconds"
                job.completed_at = now.isoformat()

                # Remove from running
                self._running_jobs.pop(agent_id, None)

                # Handle failure
                if self.on_job_failed:
                    await self.on_job_failed(job)

                # Retry if possible
                if job.can_retry:
                    job.retry_count += 1
                    await self._enqueue_job(job)

    # === Job Control ===

    def get_job(self, job_id: str) -> Job | None:
        """Get job by ID."""
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a pending or running job."""

        job = self._jobs.get(job_id)
        if not job:
            return False

        if job.is_terminal:
            return False

        job.status = JobStatus.CANCELLED
        job.error = "cancelled by user"
        job.completed_at = datetime.now(timezone.utc).isoformat()

        # Remove from running if present
        for agent_id, running_job in list(self._running_jobs.items()):
            if running_job.id == job_id:
                self._running_jobs.pop(agent_id)
                break

        return True

    def list_jobs(
        self,
        status: JobStatus | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """List jobs with optional filters."""

        jobs = list(self._jobs.values())

        if status:
            jobs = [j for j in jobs if j.status == status]

        if session_id:
            jobs = [j for j in jobs if j.session_id == session_id]

        # Sort by created_at descending
        jobs.sort(key=lambda j: j.created_at, reverse=True)

        return jobs[:limit]

    def get_stats(self) -> dict[str, Any]:
        """Get dispatcher statistics."""

        jobs = list(self._jobs.values())

        return {
            "running": len(self._running_jobs),
            "total_jobs": len(jobs),
            "pending": len([j for j in jobs if j.status == JobStatus.PENDING]),
            "completed": len([j for j in jobs if j.status == JobStatus.COMPLETED]),
            "failed": len([j for j in jobs if j.status == JobStatus.FAILED]),
            "cancelled": len([j for j in jobs if j.status == JobStatus.CANCELLED]),
            "max_concurrent": self.max_concurrent_jobs,
        }
