"""
Relay configuration model for PulseRelay.

This module defines the structured configuration schema for the Relay architecture,
including:
- Source adapter configuration
- Agent adapter configuration
- Session management settings
- Relay behavior settings
- Delivery handler configuration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ============================================================================
# Core Relay Configuration
# ============================================================================


@dataclass
class RelaySettings:
    """Top-level relay settings."""

    # Session management
    session_ttl_seconds: int = 3600
    session_idle_timeout_seconds: int = 300
    max_history_turns: int = 20

    # Behavior
    auto_create_session: bool = True
    auto_activate_session: bool = True
    auto_route: bool = True
    default_delivery: str = "bark"

    # Limits
    max_events_in_batch: int = 100
    max_concurrent_dispatches: int = 10


@dataclass
class DispatcherSettings:
    """RuntimeDispatcher settings."""

    max_concurrent_jobs: int = 10
    default_timeout_seconds: float = 300
    max_retries: int = 2


# ============================================================================
# Source Adapter Configuration
# ============================================================================


@dataclass
class SourceAdapterSettings:
    """Base settings for source adapters."""

    enabled: bool = True
    reconnect_on_error: bool = True
    reconnect_interval_seconds: float = 5
    max_reconnect_attempts: int = 10


@dataclass
class WeFlowSourceSettings(SourceAdapterSettings):
    """WeFlow source adapter specific settings."""

    host: str = "localhost"
    port: int = 5031
    access_token: str = ""
    monitor_chats: list[str] = field(default_factory=list)
    template_path: str = "templates/wx_template_example.j2"


@dataclass
class WebhookSourceSettings(SourceAdapterSettings):
    """Webhook source adapter settings."""

    path: str = "/webhook"
    secret: str = ""
    allowed_sources: list[str] = field(default_factory=list)  # IPs or hostnames


@dataclass
class WebSocketSourceSettings(SourceAdapterSettings):
    """WebSocket source adapter settings."""

    url: str = ""
    subprotocol: str = ""
    headers: dict[str, str] = field(default_factory=dict)


# ============================================================================
# Agent Adapter Configuration
# ============================================================================


@dataclass
class AgentAdapterSettings:
    """Base settings for agent adapters."""

    enabled: bool = True
    timeout_seconds: float = 300
    retry_on_error: bool = True
    max_retries: int = 2


@dataclass
class ClaudeAgentSettings(AgentAdapterSettings):
    """Claude API agent adapter settings."""

    api_key: str = ""
    api_url: str = "https://api.anthropic.com"
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.7
    max_tokens: int = 4096

    # Capability hints (actual capability determined at runtime)
    supports_streaming: bool = True
    supports_vision: bool = True
    supports_function_calling: bool = False
    supports_context_caching: bool = False
    max_context_length: int = 200000


@dataclass
class OpenAIAgentSettings(AgentAdapterSettings):
    """OpenAI agent adapter settings."""

    api_key: str = ""
    api_url: str = "https://api.openai.com"
    model: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int = 4096

    supports_streaming: bool = True
    supports_vision: bool = True
    supports_function_calling: bool = True
    max_context_length: int = 128000


@dataclass
class WebSocketAgentSettings(AgentAdapterSettings):
    """WebSocket-based agent (e.g., Claude Code) adapter settings."""

    host: str = "127.0.0.1"
    port: int = 18800
    path: str = "/ws"
    sender_id: str = "pulse_relay"
    sender_name: str = "PulseRelay"
    token: str = ""

    supports_streaming: bool = True
    max_context_length: int = 0  # Determined at runtime


# ============================================================================
# Delivery Handler Configuration
# ============================================================================


@dataclass
class DeliveryHandlerSettings:
    """Base settings for delivery handlers."""

    enabled: bool = True


@dataclass
class BarkDeliverySettings(DeliveryHandlerSettings):
    """Bark delivery handler settings."""

    device_key: str = ""
    api_url: str = "https://api.day.app"
    sound: str = "alarm"
    icon: str = ""


@dataclass
class WebSocketDeliverySettings(DeliveryHandlerSettings):
    """WebSocket delivery handler settings."""

    url: str = ""
    reconnect_on_error: bool = True


@dataclass
class FeishuDeliverySettings(DeliveryHandlerSettings):
    """Feishu (Lark) delivery handler settings."""

    webhook_url: str = ""
    secret: str = ""


# ============================================================================
# Router & Policy Configuration
# ============================================================================


@dataclass
class RouterSettings:
    """Router behavior settings."""

    # Default routing
    default_delivery: str = "bark"
    default_agent: str = ""

    # Classification rules (loaded from config)
    classification_rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PolicySettings:
    """Policy engine settings."""

    # Default risk levels by source
    source_risk_levels: dict[str, str] = field(default_factory=dict)

    # Allowed tools/agents/deliveries by source
    allowed_tools_by_source: dict[str, list[str]] = field(default_factory=dict)
    allowed_agents_by_source: dict[str, list[str]] = field(default_factory=dict)
    allowed_deliveries_by_source: dict[str, list[str]] = field(default_factory=dict)

    # Require approval flags
    require_approval_by_default: bool = False


# ============================================================================
# Aggregated Configuration
# ============================================================================


@dataclass
class SourcesConfig:
    """All source adapter configurations."""

    weflow: WeFlowSourceSettings = field(default_factory=WeFlowSourceSettings)
    webhook: WebhookSourceSettings = field(default_factory=WebhookSourceSettings)
    websocket: WebSocketSourceSettings = field(default_factory=WebSocketSourceSettings)


@dataclass
class AgentsConfig:
    """All agent adapter configurations."""

    claude: ClaudeAgentSettings = field(default_factory=ClaudeAgentSettings)
    openai: OpenAIAgentSettings = field(default_factory=OpenAIAgentSettings)
    websocket: WebSocketAgentSettings = field(default_factory=WebSocketAgentSettings)


@dataclass
class DeliveriesConfig:
    """All delivery handler configurations."""

    bark: BarkDeliverySettings = field(default_factory=BarkDeliverySettings)
    websocket: WebSocketDeliverySettings = field(default_factory=WebSocketDeliverySettings)
    feishu: FeishuDeliverySettings = field(default_factory=FeishuDeliverySettings)


@dataclass
class RelayConfig:
    """
    Complete relay configuration.

    This is the top-level configuration object that can be serialized to/from YAML.
    """

    relay: RelaySettings = field(default_factory=RelaySettings)
    dispatcher: DispatcherSettings = field(default_factory=DispatcherSettings)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    deliveries: DeliveriesConfig = field(default_factory=DeliveriesConfig)
    router: RouterSettings = field(default_factory=RouterSettings)
    policy: PolicySettings = field(default_factory=PolicySettings)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RelayConfig":
        """Create RelayConfig from a dictionary (e.g., loaded from YAML)."""

        relay_data = data.get("relay", {})
        dispatcher_data = data.get("dispatcher", {})
        sources_data = data.get("sources", {})
        agents_data = data.get("agents", {})
        deliveries_data = data.get("deliveries", {})
        router_data = data.get("router", {})
        policy_data = data.get("policy", {})

        return cls(
            relay=RelaySettings(**relay_data),
            dispatcher=DispatcherSettings(**dispatcher_data),
            sources=SourcesConfig(
                weflow=WeFlowSourceSettings(**sources_data.get("weflow", {})),
                webhook=WebhookSourceSettings(**sources_data.get("webhook", {})),
                websocket=WebSocketSourceSettings(**sources_data.get("websocket", {})),
            ),
            agents=AgentsConfig(
                claude=ClaudeAgentSettings(**agents_data.get("claude", {})),
                openai=OpenAIAgentSettings(**agents_data.get("openai", {})),
                websocket=WebSocketAgentSettings(**agents_data.get("websocket", {})),
            ),
            deliveries=DeliveriesConfig(
                bark=BarkDeliverySettings(**deliveries_data.get("bark", {})),
                websocket=WebSocketDeliverySettings(**deliveries_data.get("websocket", {})),
                feishu=FeishuDeliverySettings(**deliveries_data.get("feishu", {})),
            ),
            router=RouterSettings(**router_data),
            policy=PolicySettings(**policy_data),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""

        return {
            "relay": self.relay.__dict__,
            "dispatcher": self.dispatcher.__dict__,
            "sources": {
                "weflow": self.sources.weflow.__dict__,
                "webhook": self.sources.webhook.__dict__,
                "websocket": self.sources.websocket.__dict__,
            },
            "agents": {
                "claude": self.agents.claude.__dict__,
                "openai": self.agents.openai.__dict__,
                "websocket": self.agents.websocket.__dict__,
            },
            "deliveries": {
                "bark": self.deliveries.bark.__dict__,
                "websocket": self.deliveries.websocket.__dict__,
                "feishu": self.deliveries.feishu.__dict__,
            },
            "router": self.router.__dict__,
            "policy": self.policy.__dict__,
        }
