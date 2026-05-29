"""
Session management for PulseRelay.

SessionManager is responsible for:
- maintaining bidirectional mappings between message service sessions and agent sessions
- tracking session state (active, paused, terminated)
- storing session context and history
- handling session lifecycle (create, resume, pause, terminate)
- correlating events to sessions via conversation_id
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from core.event import EventEnvelope

SessionState = Literal[
    "pending",    # session created but not yet activated
    "active",     # actively exchanging messages
    "paused",     # temporarily suspended (e.g., waiting for human approval)
    "waiting",    # waiting for agent response
    "terminated", # session ended
    "expired",    # session timed out
]


@dataclass
class SessionContext:
    """Runtime context for a session."""

    conversation_id: str = ""        # message service conversation identifier
    source_id: str = ""             # which source adapter
    agent_id: str = ""              # which agent adapter
    user_id: str = ""
    user_name: str = ""
    project_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionHistory:
    """Stored history within a session."""

    events: list[EventEnvelope] = field(default_factory=list)
    agent_responses: list[dict[str, Any]] = field(default_factory=list)
    turn_count: int = 0
    total_chars_in: int = 0
    total_chars_out: int = 0


@dataclass
class Session:
    """A relay session bridging message service and agent runtime."""

    id: str = field(default_factory=lambda: f"sess_{uuid4().hex}")
    state: SessionState = "pending"

    context: SessionContext = field(default_factory=SessionContext)

    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_activity_at: str = ""

    # TTL and timeout settings
    ttl_seconds: int = 3600        # session expires after this many seconds
    idle_timeout_seconds: int = 300  # session pauses after this many seconds of inactivity

    history: SessionHistory = field(default_factory=SessionHistory)

    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.state == "active"

    @property
    def is_terminal(self) -> bool:
        return self.state in {"terminated", "expired"}

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.last_activity_at


@dataclass
class SessionStats:
    """Statistics for a session or session manager."""

    total_sessions: int = 0
    active_sessions: int = 0
    pending_sessions: int = 0
    waiting_sessions: int = 0
    terminated_sessions: int = 0


class SessionManager:
    """
    Manages relay sessions between message services and agent runtimes.

    Responsibilities:
    - Create/resume/pause/terminate sessions
    - Route events to the correct session based on conversation_id
    - Maintain session history
    - Handle session expiration
    - Correlate message service sessions with agent sessions
    """

    def __init__(
        self,
        default_ttl_seconds: int = 3600,
        default_idle_timeout_seconds: int = 300,
    ):
        self.default_ttl_seconds = default_ttl_seconds
        self.default_idle_timeout_seconds = default_idle_timeout_seconds

        # conversation_id -> Session
        self._sessions_by_conversation: dict[str, Session] = {}

        # session_id -> Session
        self._sessions: dict[str, Session] = {}

        # source_id -> {user_id -> session_id} (optional cross-reference)
        self._sessions_by_user: dict[str, dict[str, str]] = {}

    def create_session(
        self,
        conversation_id: str,
        source_id: str,
        agent_id: str,
        user_id: str = "",
        user_name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        """Create a new session or return existing active session for this conversation."""

        # If session already exists for this conversation, return it
        existing = self.get_session_by_conversation(conversation_id)
        if existing and existing.is_active:
            return existing

        session = Session(
            context=SessionContext(
                conversation_id=conversation_id,
                source_id=source_id,
                agent_id=agent_id,
                user_id=user_id,
                user_name=user_name,
                metadata=metadata or {},
            ),
            state="pending",
            ttl_seconds=self.default_ttl_seconds,
            idle_timeout_seconds=self.default_idle_timeout_seconds,
        )

        self._sessions[session.id] = session
        self._sessions_by_conversation[conversation_id] = session

        if user_id:
            if source_id not in self._sessions_by_user:
                self._sessions_by_user[source_id] = {}
            self._sessions_by_user[source_id][user_id] = session.id

        return session

    def get_session(self, session_id: str) -> Session | None:
        """Get session by session ID."""
        return self._sessions.get(session_id)

    def get_session_by_conversation(self, conversation_id: str) -> Session | None:
        """Get session by conversation ID from message service."""
        return self._sessions_by_conversation.get(conversation_id)

    def get_session_by_user(self, source_id: str, user_id: str) -> Session | None:
        """Get session by source and user ID."""
        session_id = self._sessions_by_user.get(source_id, {}).get(user_id)
        return self._sessions.get(session_id) if session_id else None

    def activate_session(self, session_id: str) -> Session | None:
        """Mark a session as active."""
        session = self._sessions.get(session_id)
        if session:
            session.state = "active"
            session.touch()
        return session

    def pause_session(self, session_id: str, reason: str = "") -> Session | None:
        """Pause a session (e.g., waiting for human approval)."""
        session = self._sessions.get(session_id)
        if session:
            session.state = "paused"
            session.metadata["pause_reason"] = reason
            session.touch()
        return session

    def resume_session(self, session_id: str) -> Session | None:
        """Resume a paused session."""
        session = self._sessions.get(session_id)
        if session and session.state == "paused":
            session.state = "active"
            session.touch()
        return session

    def wait_session(self, session_id: str) -> Session | None:
        """Mark session as waiting for agent response."""
        session = self._sessions.get(session_id)
        if session:
            session.state = "waiting"
            session.touch()
        return session

    def terminate_session(self, session_id: str, reason: str = "") -> Session | None:
        """Terminate a session."""
        session = self._sessions.get(session_id)
        if session:
            session.state = "terminated"
            session.metadata["termination_reason"] = reason
            session.touch()
            self._cleanup_session_references(session)
        return session

    def _cleanup_session_references(self, session: Session):
        """Remove session from lookup dictionaries."""
        self._sessions.pop(session.id, None)
        self._sessions_by_conversation.pop(session.context.conversation_id, None)

        source_id = session.context.source_id
        user_id = session.context.user_id
        if source_id and user_id:
            self._sessions_by_user.get(source_id, {}).pop(user_id, None)

    def add_event_to_history(self, session_id: str, event: EventEnvelope):
        """Append an inbound event to session history."""
        session = self._sessions.get(session_id)
        if session:
            session.history.events.append(event)
            session.history.total_chars_in += len(event.content.text)
            session.touch()

    def add_response_to_history(self, session_id: str, response: dict[str, Any]):
        """Append an agent response to session history."""
        session = self._sessions.get(session_id)
        if session:
            session.history.agent_responses.append(response)
            session.history.total_chars_out += len(response.get("content", ""))
            session.history.turn_count += 1
            session.touch()

    def get_conversation_history(
        self,
        session_id: str,
        max_turns: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Get formatted conversation history for sending to agent.

        max_turns: if > 0, limit to last N turns
        """

        session = self._sessions.get(session_id)
        if not session:
            return []

        history: list[dict[str, Any]] = []

        # Add event history
        for event in session.history.events:
            history.append({
                "role": "user",
                "content": event.content.text,
                "timestamp": event.event.timestamp,
            })

        # Add agent response history
        for response in session.history.agent_responses:
            history.append({
                "role": "assistant",
                "content": response.get("content", ""),
                "timestamp": response.get("timestamp", ""),
            })

        if max_turns > 0:
            history = history[-max_turns * 2:]  # Each turn has 2 entries

        return history

    def check_idle_sessions(self) -> list[str]:
        """
        Check for sessions that have exceeded idle timeout.
        Returns list of session IDs to expire.
        """

        now = datetime.now(timezone.utc)
        expired_ids: list[str] = []

        for session in self._sessions.values():
            if session.state not in {"active", "waiting"}:
                continue

            if not session.last_activity_at:
                continue

            last_activity = datetime.fromisoformat(session.last_activity_at)
            idle_seconds = (now - last_activity).total_seconds()

            if idle_seconds > session.idle_timeout_seconds:
                session.state = "paused"
                session.metadata["pause_reason"] = "idle_timeout"
                expired_ids.append(session.id)

        return expired_ids

    def check_expired_sessions(self) -> list[str]:
        """
        Check for sessions that have exceeded TTL.
        Returns list of session IDs to terminate.
        """

        now = datetime.now(timezone.utc)
        expired_ids: list[str] = []

        for session in self._sessions.values():
            if session.is_terminal:
                continue

            created = datetime.fromisoformat(session.created_at)
            age_seconds = (now - created).total_seconds()

            if age_seconds > session.ttl_seconds:
                session.state = "expired"
                session.metadata["termination_reason"] = "ttl_expired"
                expired_ids.append(session.id)
                self._cleanup_session_references(session)

        return expired_ids

    def list_sessions(
        self,
        state: SessionState | None = None,
        source_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[Session]:
        """List sessions with optional filters."""

        sessions = list(self._sessions.values())

        if state:
            sessions = [s for s in sessions if s.state == state]

        if source_id:
            sessions = [s for s in sessions if s.context.source_id == source_id]

        if agent_id:
            sessions = [s for s in sessions if s.context.agent_id == agent_id]

        return sessions

    def get_stats(self) -> SessionStats:
        """Get session statistics."""

        sessions = list(self._sessions.values())

        return SessionStats(
            total_sessions=len(sessions),
            active_sessions=len([s for s in sessions if s.state == "active"]),
            pending_sessions=len([s for s in sessions if s.state == "pending"]),
            waiting_sessions=len([s for s in sessions if s.state == "waiting"]),
            terminated_sessions=len([s for s in sessions if s.is_terminal]),
        )
