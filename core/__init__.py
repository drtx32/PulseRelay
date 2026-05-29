"""
PulseRelay core module.

Stable core exports plus Phase 6 runtime dispatcher primitives.

Keep this package initializer conservative: optional or later-phase modules
should be imported explicitly from their own modules to avoid import-time
coupling and missing dependency failures.
"""

from core.event import (
    EventEnvelope,
    EventSource,
    EventSender,
    EventMeta,
    EventContent,
    EventContext,
    EventPermissions,
    EventRouting,
    RiskLevel,
    Priority,
    TrustLevel,
    ensure_event,
)

from core.event_bus import EventBus, EventRecord

from core.source_adapter import (
    SourceAdapter,
    SourceRegistry,
    SourceManifest,
    SourceCapabilities,
    SourceHealth,
    SourceState,
)

from core.delivery_handler import (
    DeliveryHandler,
    DeliveryRegistry,
    DeliveryManifest,
    DeliveryCapabilities,
    DeliveryMessage,
    DeliveryResult,
    DeliveryState,
    DeliveryStatus,
)

from core.router import Router, RouteDecision, RouteTarget
from core.policy import PolicyEngine, PolicyDecision, PolicyContext, PolicyStatus, RISK_ORDER
from core.trigger_engine import TriggerEngine, TriggerConfig, TriggerResult, Message

from core.agent_adapter import (
    AgentAdapter,
    AgentRegistry,
    AgentManifest,
    AgentCapabilities,
    AgentHealth,
    AgentState,
    AgentRequest,
    AgentResponse,
    OpenClawAdapter,
)

from core.session_manager import (
    SessionManager,
    Session,
    SessionState,
    SessionContext,
    SessionHistory,
    SessionStats,
)

from core.runtime_dispatcher import RuntimeDispatcher

from core.job import (
    Job,
    JobStatus,
    JobPriority,
    JobRequirements,
    DispatchResult,
)

__all__ = [
    "EventEnvelope",
    "EventSource",
    "EventSender",
    "EventMeta",
    "EventContent",
    "EventContext",
    "EventPermissions",
    "EventRouting",
    "RiskLevel",
    "Priority",
    "TrustLevel",
    "ensure_event",
    "EventBus",
    "EventRecord",
    "SourceAdapter",
    "SourceRegistry",
    "SourceManifest",
    "SourceCapabilities",
    "SourceHealth",
    "SourceState",
    "DeliveryHandler",
    "DeliveryRegistry",
    "DeliveryManifest",
    "DeliveryCapabilities",
    "DeliveryMessage",
    "DeliveryResult",
    "DeliveryState",
    "DeliveryStatus",
    "Router",
    "RouteDecision",
    "RouteTarget",
    "PolicyEngine",
    "PolicyDecision",
    "PolicyContext",
    "PolicyStatus",
    "RISK_ORDER",
    "TriggerEngine",
    "TriggerConfig",
    "TriggerResult",
    "Message",
    "AgentAdapter",
    "AgentRegistry",
    "AgentManifest",
    "AgentCapabilities",
    "AgentHealth",
    "AgentState",
    "AgentRequest",
    "AgentResponse",
    "OpenClawAdapter",
    "SessionManager",
    "Session",
    "SessionState",
    "SessionContext",
    "SessionHistory",
    "SessionStats",
    "RuntimeDispatcher",
    "Job",
    "JobStatus",
    "JobPriority",
    "JobRequirements",
    "DispatchResult",
]
