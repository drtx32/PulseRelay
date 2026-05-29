"""
Job abstraction for PulseRelay Phase 6.

A Job represents a unit of work to be dispatched to an agent runtime.
Jobs track their lifecycle from submission through completion.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class JobPriority(Enum):
    """Job priority levels."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


class JobStatus(Enum):
    """Job lifecycle status."""

    PENDING = "pending"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class JobRequirements:
    """Requirements/constraints for job execution."""

    required_capabilities: list[str] = field(default_factory=list)  # e.g., ["streaming", "vision"]
    required_agents: list[str] = field(default_factory=list)  # specific agent IDs
    excluded_agents: list[str] = field(default_factory=list)  # agents to avoid
    max_context_length: int = 0  # minimum context window needed
    priority: JobPriority = JobPriority.NORMAL


@dataclass
class Job:
    """
    A unit of work to be dispatched to an agent.

    The Job class tracks the full execution lifecycle from submission
    through completion (or failure/timeout/cancellation).
    """

    id: str = field(default_factory=lambda: f"job_{uuid4().hex}")
    status: JobStatus = JobStatus.PENDING

    # What to run - agent request
    request: Any = None  # AgentRequest
    session_id: str = ""

    # Requirements
    requirements: JobRequirements = field(default_factory=JobRequirements)

    # Scheduling
    priority: JobPriority = JobPriority.NORMAL
    scheduled_at: str = ""
    started_at: str = ""
    completed_at: str = ""

    # Limits
    timeout_seconds: float = 300  # 5 minutes default
    retry_count: int = 0
    max_retries: int = 2

    # Results
    response: Any = None  # AgentResponse
    error: str = ""

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """Check if job is in a terminal state."""
        return self.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.TIMEOUT,
        }

    @property
    def can_retry(self) -> bool:
        """Check if job can be retried."""
        return (
            self.retry_count < self.max_retries
            and self.status in {JobStatus.FAILED, JobStatus.TIMEOUT}
        )


@dataclass
class DispatchResult:
    """Result of a dispatch operation."""

    job_id: str
    success: bool
    agent_id: str = ""
    error: str = ""
    queued: bool = False  # True if job was queued rather than dispatched immediately
