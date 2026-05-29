"""
MessageRelay - Core routing coordinator for PulseRelay.

MessageRelay orchestrates the flow:
1. Receives normalized events from EventBus
2. Correlates events to sessions via SessionManager
3. Routes events through Router/PolicyEngine
4. Dispatches to AgentAdapter
5. Handles agent responses
6. Delivers results via DeliveryHandler

It acts as the central coordinator that binds all other components together.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Callable, Awaitable
from uuid import uuid4

from core.event import EventEnvelope, ensure_event, EventContent, EventContext
from core.event_bus import EventBus
from core.router import Router, RouteDecision
from core.policy import PolicyEngine, PolicyContext
from core.session_manager import SessionManager, Session, SessionState
from core.agent_adapter import AgentAdapter, AgentRequest, AgentResponse, AgentRegistry
from core.delivery_handler import DeliveryHandler, DeliveryMessage, DeliveryResult, DeliveryRegistry

logger = logging.getLogger(__name__)

RelayState = Literal[
    "initialized",
    "running",
    "paused",
    "stopped",
    "error",
]


@dataclass
class RelayConfig:
    """Configuration for MessageRelay behavior."""

    # Session settings
    session_ttl_seconds: int = 3600
    session_idle_timeout_seconds: int = 300

    # Relay behavior
    auto_create_session: bool = True
    auto_activate_session: bool = True
    auto_route: bool = True

    # History limits
    max_history_turns: int = 20  # max conversation turns to send to agent
    max_events_in_batch: int = 100

    # Delivery
    default_delivery: str = "bark"


@dataclass
class RelayStats:
    """Runtime statistics for MessageRelay."""

    events_received: int = 0
    events_routed: int = 0
    events_blocked: int = 0
    events_delivered: int = 0
    sessions_created: int = 0
    agent_requests: int = 0
    agent_errors: int = 0
    last_event_at: str = ""


class MessageRelay:
    """
    Core message routing coordinator.

    MessageRelay binds together:
    - EventBus: event input
    - SessionManager: session state
    - Router: routing decisions
    - PolicyEngine: policy evaluation
    - AgentRegistry: agent runtimes
    - DeliveryRegistry: delivery handlers

    Flow for a typical event:

    1. Event arrives via EventBus (or direct call to relay_event)
    2. SessionManager.find_or_create_session(event)
    3. Router.route(event) -> RouteDecision
    4. PolicyEngine.evaluate(event, context) -> PolicyDecision
    5. If blocked: deliver rejection via DeliveryHandler
    6. If approved:
       a. Build AgentRequest from event + session history
       b. AgentAdapter.send_request(request)
       c. On response: SessionManager.add_response_to_history
       d. Build DeliveryMessage from agent response
       e. DeliveryHandler.deliver(message)
    7. Update session state
    """

    def __init__(
        self,
        event_bus: EventBus,
        session_manager: SessionManager | None = None,
        router: Router | None = None,
        policy_engine: PolicyEngine | None = None,
        agent_registry: AgentRegistry | None = None,
        delivery_registry: DeliveryRegistry | None = None,
        config: RelayConfig | None = None,
    ):
        self.event_bus = event_bus
        self.session_manager = session_manager or SessionManager()
        self.router = router or Router()
        self.policy_engine = policy_engine or PolicyEngine()
        self.agent_registry = agent_registry or AgentRegistry()
        self.delivery_registry = delivery_registry or DeliveryRegistry()
        self.config = config or RelayConfig()

        self.state: RelayState = "initialized"
        self.stats = RelayStats()

        self._consume_task: asyncio.Task | None = None
        self._running = False

        # Hooks for external customization
        self.on_event_received: Callable[[EventEnvelope], Awaitable[None]] | None = None
        self.on_route_decision: Callable[[EventEnvelope, RouteDecision], Awaitable[None]] | None = None
        self.on_agent_request: Callable[[AgentRequest], Awaitable[None]] | None = None
        self.on_agent_response: Callable[[AgentResponse], Awaitable[None]] | None = None
        self.on_delivery_result: Callable[[DeliveryResult], Awaitable[None]] | None = None

    # === Lifecycle ===

    async def start(self):
        """Start the relay consumer loop."""

        if self.state == "running":
            return

        self.state = "running"
        self._running = True
        self._consume_task = asyncio.create_task(self._consume_loop())
        logger.info("MessageRelay started")

    async def stop(self):
        """Stop the relay consumer loop."""

        self._running = False

        if self._consume_task:
            self._consume_task.cancel()
            try:
                await self._consume_task
            except asyncio.CancelledError:
                pass
            self._consume_task = None

        self.state = "stopped"
        logger.info("MessageRelay stopped")

    async def _consume_loop(self):
        """Main event consumption loop."""

        while self._running:
            try:
                record = self.event_bus.get(timeout=0.5)
                if record:
                    await self.relay_event(record.event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in consume loop: {e}")
                await asyncio.sleep(0.1)

    # === Public API ===

    async def relay_event(self, event: EventEnvelope | dict[str, Any]) -> RouteDecision | None:
        """
        Process an incoming event through the relay pipeline.

        Returns the RouteDecision if processed, None if skipped/blocked.
        """

        normalized = ensure_event(event)
        self.stats.events_received += 1
        self.stats.last_event_at = datetime.now(timezone.utc).isoformat()

        if self.on_event_received:
            await self.on_event_received(normalized)

        # Find or create session
        session = self._get_or_create_session(normalized)
        if not session:
            logger.warning(f"No session found/creatable for event {normalized.id}")
            return None

        # Add event to session history
        self.session_manager.add_event_to_history(session.id, normalized)

        # Make routing decision
        route_decision = self.router.route(normalized)

        if self.on_route_decision:
            await self.on_route_decision(normalized, route_decision)

        if not route_decision.accepted:
            self.stats.events_blocked += 1
            await self._deliver_rejection(normalized, route_decision)
            return route_decision

        # Evaluate policy
        policy_context = PolicyContext(
            requested_risk_level=route_decision.risk_level,
            requested_agents=[route_decision.target_agent] if route_decision.target_agent else [],
            requested_deliveries=[route_decision.target_delivery] if route_decision.target_delivery else [],
        )

        policy_decision = self.policy_engine.evaluate(normalized, policy_context)
        route_decision.policy = policy_decision

        if not policy_decision.allowed:
            self.stats.events_blocked += 1
            await self._deliver_rejection(normalized, route_decision, policy_decision)
            return route_decision

        # Activate session if pending
        if session.state == "pending" and self.config.auto_activate_session:
            self.session_manager.activate_session(session.id)

        # Dispatch to agent if target_agent specified
        if route_decision.target_agent:
            await self._dispatch_to_agent(session, normalized, route_decision)
        elif route_decision.target_delivery:
            await self._deliver_direct(normalized, route_decision)

        self.stats.events_routed += 1
        return route_decision

    async def relay_response(
        self,
        session_id: str,
        response: AgentResponse,
    ) -> DeliveryResult | None:
        """
        Handle an agent response flowing back through the relay.

        Called when an agent responds to a request we dispatched.
        """

        session = self.session_manager.get_session(session_id)
        if not session:
            logger.warning(f"Session not found for response: {session_id}")
            return None

        # Add response to session history
        self.session_manager.add_response_to_history(session_id, {
            "content": response.content,
            "done": response.done,
            "error": response.error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        if self.on_agent_response:
            await self.on_agent_response(response)

        if response.error:
            self.stats.agent_errors += 1
            # Deliver error notification
            return await self._deliver_error(session, response)

        # Deliver successful response
        delivery = self._build_delivery_message(session, response)
        handler = self.delivery_registry.get(self.config.default_delivery)

        if handler:
            result = await handler.deliver(delivery)
            self.stats.events_delivered += 1
            if self.on_delivery_result:
                await self.on_delivery_result(result)
            return result

        return None

    # === Internal methods ===

    def _get_or_create_session(self, event: EventEnvelope) -> Session | None:
        """Find existing session or create new one for this event."""

        conversation_id = event.context.conversation_id or event.source.id

        # Try to find existing session
        session = self.session_manager.get_session_by_conversation(conversation_id)

        if session:
            return session

        if not self.config.auto_create_session:
            return None

        # Determine target agent from routing hints
        target_agent = event.routing.target_agent or ""

        session = self.session_manager.create_session(
            conversation_id=conversation_id,
            source_id=event.source.type,
            agent_id=target_agent,
            user_id=event.sender.id,
            user_name=event.sender.name,
            metadata={
                "event_id": event.id,
                "source_name": event.source.name,
            },
        )

        self.stats.sessions_created += 1
        return session

    async def _dispatch_to_agent(
        self,
        session: Session,
        event: EventEnvelope,
        route_decision: RouteDecision,
    ):
        """Send event to agent adapter and handle response."""

        agent_id = route_decision.target_agent
        agent = self.agent_registry.get(agent_id)

        if not agent:
            logger.error(f"Agent not found: {agent_id}")
            await self._deliver_error(session, AgentResponse(error=f"Agent not found: {agent_id}"))
            return

        # Build agent request
        history = self.session_manager.get_conversation_history(
            session.id,
            max_turns=self.config.max_history_turns,
        )

        # Build context from event
        context = {
            "session_id": session.id,
            "source_id": event.source.type,
            "source_name": event.source.name,
            "conversation_id": event.context.conversation_id,
            "sender_id": event.sender.id,
            "sender_name": event.sender.name,
            "routing_labels": route_decision.labels,
            "risk_level": route_decision.risk_level,
        }

        request = AgentRequest(
            prompt=event.content.text,
            messages=history,
            context=context,
            metadata={
                "event_id": event.id,
                "route_decision_id": route_decision.id,
            },
        )

        if self.on_agent_request:
            await self.on_agent_request(request)

        self.stats.agent_requests += 1

        # Mark session as waiting
        self.session_manager.wait_session(session.id)

        # Send to agent
        response = await agent.send_request(request)

        # Handle response
        await self.relay_response(session.id, response)

        # Return session to active state
        if session.state == "waiting":
            self.session_manager.activate_session(session.id)

    async def _deliver_rejection(
        self,
        event: EventEnvelope,
        route_decision: RouteDecision,
        policy_decision=None,
    ):
        """Deliver a rejection message when event is blocked."""

        reason = policy_decision.reason if policy_decision else route_decision.reason

        message = DeliveryMessage(
            title="Event Blocked",
            text=f"Your request was not approved.\n\nReason: {reason}",
            metadata={
                "event_id": event.id,
                "route_decision_id": route_decision.id,
                "policy_decision_id": policy_decision.id if policy_decision else None,
            },
        )

        handler = self.delivery_registry.get(self.config.default_delivery)
        if handler:
            result = await handler.deliver(message)
            if self.on_delivery_result:
                await self.on_delivery_result(result)

    async def _deliver_direct(
        self,
        event: EventEnvelope,
        route_decision: RouteDecision,
    ):
        """Deliver event directly to delivery handler without agent."""

        delivery = DeliveryMessage.from_event(event)

        handler_id = route_decision.target_delivery or self.config.default_delivery
        handler = self.delivery_registry.get(handler_id)

        if handler:
            result = await handler.deliver(delivery)
            self.stats.events_delivered += 1
            if self.on_delivery_result:
                await self.on_delivery_result(result)

    async def _deliver_error(self, session: Session, response: AgentResponse) -> DeliveryResult | None:
        """Deliver an error notification."""

        message = DeliveryMessage(
            title="Agent Error",
            text=f"An error occurred while processing your request.\n\n{response.error}",
            metadata={
                "session_id": session.id,
                "response_id": response.id,
            },
        )

        handler = self.delivery_registry.get(self.config.default_delivery)
        if handler:
            result = await handler.deliver(message)
            if self.on_delivery_result:
                await self.on_delivery_result(result)
            return result

        return None

    def _build_delivery_message(self, session: Session, response: AgentResponse) -> DeliveryMessage:
        """Build a delivery message from agent response."""

        return DeliveryMessage(
            title=f"Response from {session.context.agent_id}",
            text=response.content,
            metadata={
                "session_id": session.id,
                "agent_id": session.context.agent_id,
                "response_id": response.id,
                "turn_count": session.history.turn_count,
            },
        )

    # === Admin ===

    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive relay statistics."""

        return {
            "state": self.state,
            "stats": self.stats.__dict__,
            "session_stats": self.session_manager.get_stats().__dict__,
            "agents": self.agent_registry.health_snapshot(),
            "deliveries": self.delivery_registry.state_snapshot(),
        }

    async def check_health(self) -> dict[str, Any]:
        """Perform health check across all components."""

        health = {
            "relay": {
                "state": self.state,
                "running": self._running,
            },
            "sessions": self.session_manager.get_stats().__dict__,
            "agents": self.agent_registry.health_snapshot(),
            "deliveries": self.delivery_registry.state_snapshot(),
        }

        # Check for idle/expired sessions
        expired = self.session_manager.check_expired_sessions()
        if expired:
            health["session_cleanup"] = {"expired": expired}

        paused = self.session_manager.check_idle_sessions()
        if paused:
            health["session_cleanup"] = health.get("session_cleanup", {})
            health["session_cleanup"]["paused"] = paused

        return health
