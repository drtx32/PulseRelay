"""
PulseRelay core module.

Core components:
- event: EventEnvelope and related event primitives
- event_bus: EventBus for event queuing
- source_adapter: SourceAdapter ABC and SourceRegistry
- delivery_handler: DeliveryHandler ABC and DeliveryRegistry
- router: Router for event classification and routing
- policy: PolicyEngine for access control
- trigger_engine: TriggerEngine for event aggregation
- agent_adapter: AgentAdapter ABC and AgentRegistry
- session_manager: SessionManager for session lifecycle
- message_relay: MessageRelay core coordinator
- runtime_dispatcher: RuntimeDispatcher for job scheduling
- relay_config: Configuration models
- plugin: Plugin system for extensibility (Phase 7)
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
)

from core.session_manager import (
    SessionManager,
    Session,
    SessionState,
    SessionContext,
    SessionHistory,
    SessionStats,
)

from core.message_relay import (
    MessageRelay,
    RelayConfig,
    RelayState,
    RelayStats,
)

from core.runtime_dispatcher import (
    RuntimeDispatcher,
)

from core.job import (
    Job,
    JobStatus,
    JobPriority,
    JobRequirements,
    DispatchResult,
)

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
    OpenAIAdapter,
)

from core.relay_config import (
    RelaySettings,
    DispatcherSettings,
    SourceAdapterSettings,
    WeFlowSourceSettings,
    WebhookSourceSettings,
    WebSocketSourceSettings,
    AgentAdapterSettings,
    ClaudeAgentSettings,
    OpenAIAgentSettings,
    WebSocketAgentSettings,
    DeliveryHandlerSettings,
    BarkDeliverySettings,
    WebSocketDeliverySettings,
    FeishuDeliverySettings,
    RouterSettings,
    PolicySettings,
    SourcesConfig,
    AgentsConfig,
    DeliveriesConfig,
    RelayConfig as RelayConfigModel,
)

# Plugin system (Phase 7)
from core.plugin import (
    Plugin,
    PluginRegistry,
    PluginContext,
)
from core.plugin.base import (
    SourcePlugin,
    DeliveryPlugin,
    AgentPlugin,
    RouterPlugin,
    PolicyPlugin,
)
from core.plugin.manifest import (
    PluginManifest,
    PluginMetadata,
    PluginCapabilities,
    PluginState,
    PluginEntryPoint,
)
from core.plugin.loader import PluginLoader, PluginLoadSpec
from core.plugin.sandbox import PluginSandbox, SandboxedPluginRunner, create_default_sandbox
from core.plugin.hot_reload import HotReloader, HotReloadMixin

__all__ = [
    # Event primitives
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
    # Event bus
    "EventBus",
    "EventRecord",
    # Source adapter
    "SourceAdapter",
    "SourceRegistry",
    "SourceManifest",
    "SourceCapabilities",
    "SourceHealth",
    "SourceState",
    # Delivery handler
    "DeliveryHandler",
    "DeliveryRegistry",
    "DeliveryManifest",
    "DeliveryCapabilities",
    "DeliveryMessage",
    "DeliveryResult",
    "DeliveryState",
    "DeliveryStatus",
    # Router
    "Router",
    "RouteDecision",
    "RouteTarget",
    # Policy
    "PolicyEngine",
    "PolicyDecision",
    "PolicyContext",
    "PolicyStatus",
    "RISK_ORDER",
    # Trigger engine
    "TriggerEngine",
    "TriggerConfig",
    "TriggerResult",
    "Message",
    # Agent adapter (NEW)
    "AgentAdapter",
    "AgentRegistry",
    "AgentManifest",
    "AgentCapabilities",
    "AgentHealth",
    "AgentState",
    "AgentRequest",
    "AgentResponse",
    # Session manager (NEW)
    "SessionManager",
    "Session",
    "SessionState",
    "SessionContext",
    "SessionHistory",
    "SessionStats",
    # Message relay (NEW)
    "MessageRelay",
    "RelayConfig",
    "RelayState",
    "RelayStats",
    # Runtime dispatcher (NEW)
    "RuntimeDispatcher",
    "Job",
    "JobStatus",
    "JobPriority",
    "JobRequirements",
    "DispatchResult",
    # Relay config (NEW)
    "RelaySettings",
    "DispatcherSettings",
    "SourceAdapterSettings",
    "WeFlowSourceSettings",
    "WebhookSourceSettings",
    "WebSocketSourceSettings",
    "AgentAdapterSettings",
    "ClaudeAgentSettings",
    "OpenAIAgentSettings",
    "WebSocketAgentSettings",
    "DeliveryHandlerSettings",
    "BarkDeliverySettings",
    "WebSocketDeliverySettings",
    "FeishuDeliverySettings",
    "RouterSettings",
    "PolicySettings",
    "SourcesConfig",
    "AgentsConfig",
    "DeliveriesConfig",
    "RelayConfigModel",
    # Plugin system (Phase 7)
    "Plugin",
    "PluginRegistry",
    "PluginContext",
    "SourcePlugin",
    "DeliveryPlugin",
    "AgentPlugin",
    "RouterPlugin",
    "PolicyPlugin",
    "PluginManifest",
    "PluginMetadata",
    "PluginCapabilities",
    "PluginState",
    "PluginEntryPoint",
    "PluginLoader",
    "PluginLoadSpec",
    "PluginSandbox",
    "SandboxedPluginRunner",
    "create_default_sandbox",
    "HotReloader",
    "HotReloadMixin",
]
