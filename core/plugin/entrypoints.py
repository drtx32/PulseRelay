"""
Plugin entry point definitions.

Entry points define the slots where plugins can be plugged into the
PulseRelay architecture. This is the canonical source of truth for
entry point types.
"""

from __future__ import annotations

from enum import Enum


class PluginEntryPoint(Enum):
    """
    Entry point types that a plugin can provide.

    Each entry point corresponds to a slot in the PulseRelay architecture:
    - SOURCE: Event source adapters (implements SourceAdapter)
    - DELIVERY: Event delivery handlers (implements DeliveryHandler)
    - AGENT: AI agent runtime adapters (implements AgentAdapter)
    - ROUTER: Custom routing logic (implements Router)
    - POLICY: Custom policy engine (implements PolicyEngine)
    """

    SOURCE = "source"
    DELIVERY = "delivery"
    AGENT = "agent"
    ROUTER = "router"
    POLICY = "policy"

    @classmethod
    def from_string(cls, value: str) -> "PluginEntryPoint":
        """
        Create an entry point from a string value.

        Args:
            value: String value (e.g., "source", "delivery")

        Returns:
            PluginEntryPoint enum value

        Raises:
            ValueError: If the value is not a valid entry point
        """
        try:
            return cls(value.lower())
        except ValueError:
            valid = [ep.value for ep in cls]
            raise ValueError(f"Invalid entry point '{value}'. Valid values: {valid}")

    @property
    def manifest_key(self) -> str:
        """Return the key used in plugin manifests."""
        return f"{self.value}_plugins"

    @property
    def registry_key(self) -> str:
        """Return the key used in registries."""
        return f"{self.value}_registry"

    @property
    def interface_class(self) -> type | None:
        """
        Return the interface class for this entry point.

        Returns None if no direct mapping exists.
        """
        # This is a lazy import to avoid circular dependencies
        from core.source_adapter import SourceAdapter
        from core.delivery_handler import DeliveryHandler
        from core.agent_adapter import AgentAdapter

        mapping = {
            PluginEntryPoint.SOURCE: SourceAdapter,
            PluginEntryPoint.DELIVERY: DeliveryHandler,
            PluginEntryPoint.AGENT: AgentAdapter,
        }
        return mapping.get(self)

    @property
    def interface_module(self) -> str:
        """Return the module path for the interface class."""
        from core.source_adapter import SourceAdapter
        from core.delivery_handler import DeliveryHandler
        from core.agent_adapter import AgentAdapter

        mapping = {
            PluginEntryPoint.SOURCE: "core.source_adapter",
            PluginEntryPoint.DELIVERY: "core.delivery_handler",
            PluginEntryPoint.AGENT: "core.agent_adapter",
            PluginEntryPoint.ROUTER: "core.router",
            PluginEntryPoint.POLICY: "core.policy",
        }
        return mapping.get(self, "")


# ============================================================================
# Entry Point Patterns (AstrBot-style)
# ============================================================================

# These are reference patterns showing how different entry points map
# to PulseRelay components:

ENTRY_POINT_PATTERNS = {
    PluginEntryPoint.SOURCE: {
        "interface": "SourceAdapter",
        "module": "core.source_adapter",
        "manifest_field": "source_adapter_class",
        "registry": "source_registry",
        "example": "TelegramSource, WeFlowSource, SlackSource",
    },
    PluginEntryPoint.DELIVERY: {
        "interface": "DeliveryHandler",
        "module": "core.delivery_handler",
        "manifest_field": "delivery_handler_class",
        "registry": "delivery_registry",
        "example": "BarkDelivery, WebhookDelivery, SlackDelivery",
    },
    PluginEntryPoint.AGENT: {
        "interface": "AgentAdapter",
        "module": "core.agent_adapter",
        "manifest_field": "agent_adapter_class",
        "registry": "agent_registry",
        "example": "OpenClawAdapter, ClaudeAdapter, OpenAIAdapter",
    },
    PluginEntryPoint.ROUTER: {
        "interface": "Router",
        "module": "core.router",
        "manifest_field": "router_class",
        "registry": "router",
        "example": "CustomRouter, AIRouter",
    },
    PluginEntryPoint.POLICY: {
        "interface": "PolicyEngine",
        "module": "core.policy",
        "manifest_field": "policy_class",
        "registry": "policy_engine",
        "example": "CustomPolicyEngine, AIBasedPolicy",
    },
}
