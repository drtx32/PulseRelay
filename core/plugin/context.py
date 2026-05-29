"""
Plugin context - APIs exposed to plugins.

PluginContext provides plugins with access to PulseRelay core functionality,
such as the event bus, registries, and configuration. This is similar to
AstrBot's context.py that exposes APIs to Stars.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.event_bus import EventBus
    from core.source_adapter import SourceRegistry
    from core.delivery_handler import DeliveryRegistry
    from core.agent_adapter import AgentRegistry
    from core.router import Router
    from core.policy import PolicyEngine
    from core.plugin.registry import PluginRegistry


logger = logging.getLogger(__name__)


class PluginContext:
    """
    Context object passed to plugins providing access to core services.

    Plugins receive an instance of this class during initialization,
    giving them access to:
    - EventBus for publishing events
    - Registries for discovering other plugins
    - Configuration
    - Logging

    Example:
        class MySourcePlugin(SourcePlugin):
            def initialize(self, ctx: PluginContext) -> None:
                self.ctx = ctx
                self.ctx.event_bus  # Access to event bus
                self.ctx.get_delivery("bark")  # Get a delivery handler
    """

    def __init__(
        self,
        plugin_id: str,
        event_bus: "EventBus | None" = None,
        source_registry: "SourceRegistry | None" = None,
        delivery_registry: "DeliveryRegistry | None" = None,
        agent_registry: "AgentRegistry | None" = None,
        router: "Router | None" = None,
        policy_engine: "PolicyEngine | None" = None,
        plugin_registry: "PluginRegistry | None" = None,
        config: dict[str, Any] | None = None,
    ):
        self._plugin_id = plugin_id
        self._event_bus = event_bus
        self._source_registry = source_registry
        self._delivery_registry = delivery_registry
        self._agent_registry = agent_registry
        self._router = router
        self._policy_engine = policy_engine
        self._plugin_registry = plugin_registry
        self._config = config or {}
        self._log = logging.getLogger(f"plugin.{plugin_id}")

    @property
    def plugin_id(self) -> str:
        """The plugin's unique identifier."""
        return self._plugin_id

    @property
    def event_bus(self) -> "EventBus | None":
        """The central event bus for publishing events."""
        return self._event_bus

    @property
    def source_registry(self) -> "SourceRegistry | None":
        """Registry for source adapters."""
        return self._source_registry

    @property
    def delivery_registry(self) -> "DeliveryRegistry | None":
        """Registry for delivery handlers."""
        return self._delivery_registry

    @property
    def agent_registry(self) -> "AgentRegistry | None":
        """Registry for agent adapters."""
        return self._agent_registry

    @property
    def router(self) -> "Router | None":
        """The router instance."""
        return self._router

    @property
    def policy_engine(self) -> "PolicyEngine | None":
        """The policy engine instance."""
        return self._policy_engine

    @property
    def plugin_registry(self) -> "PluginRegistry | None":
        """The plugin registry instance."""
        return self._plugin_registry

    @property
    def config(self) -> dict[str, Any]:
        """Plugin configuration."""
        return self._config

    @property
    def log(self) -> logging.Logger:
        """Logger for the plugin."""
        return self._log

    # -------------------------------------------------------------------------
    # Convenience Methods
    # -------------------------------------------------------------------------

    def get_source(self, source_id: str) -> Any | None:
        """
        Get a source adapter by ID.

        Args:
            source_id: The source adapter identifier

        Returns:
            SourceAdapter instance or None if not found
        """
        if self._source_registry is None:
            return None
        return self._source_registry.get(source_id)

    def get_delivery(self, delivery_id: str) -> Any | None:
        """
        Get a delivery handler by ID.

        Args:
            delivery_id: The delivery handler identifier

        Returns:
            DeliveryHandler instance or None if not found
        """
        if self._delivery_registry is None:
            return None
        return self._delivery_registry.get(delivery_id)

    def get_agent(self, agent_id: str) -> Any | None:
        """
        Get an agent adapter by ID.

        Args:
            agent_id: The agent adapter identifier

        Returns:
            AgentAdapter instance or None if not found
        """
        if self._agent_registry is None:
            return None
        return self._agent_registry.get(agent_id)

    def list_sources(self) -> list[Any]:
        """List all registered source adapters."""
        if self._source_registry is None:
            return []
        return self._source_registry.list()

    def list_deliveries(self) -> list[Any]:
        """List all registered delivery handlers."""
        if self._delivery_registry is None:
            return []
        return self._delivery_registry.list()

    def list_agents(self) -> list[Any]:
        """List all registered agent adapters."""
        if self._agent_registry is None:
            return []
        return self._agent_registry.list()

    def list_plugins(
        self,
        entry_point: str | None = None,
        enabled: bool | None = None,
    ) -> list[Any]:
        """
        List plugins with optional filtering.

        Args:
            entry_point: Filter by entry point type (e.g., "source", "delivery")
            enabled: Filter by enabled state

        Returns:
            List of PluginManifest objects
        """
        if self._plugin_registry is None:
            return []

        from core.plugin.manifest import PluginEntryPoint

        ep = None
        if entry_point:
            try:
                ep = PluginEntryPoint(entry_point)
            except ValueError:
                pass

        return self._plugin_registry.list(
            entry_point=ep,
            enabled=enabled,
        )

    # -------------------------------------------------------------------------
    # Event Publishing
    # -------------------------------------------------------------------------

    async def publish_event(self, event: Any) -> None:
        """
        Publish an event to the event bus.

        Args:
            event: EventEnvelope to publish
        """
        if self._event_bus is not None:
            source_id = getattr(event, "source_type", self._plugin_id)
            self._event_bus.put(source_id, event)
            self._log.debug(f"Published event: {event}")
        else:
            self._log.warning("Event bus not available")

    # -------------------------------------------------------------------------
    # Configuration Helpers
    # -------------------------------------------------------------------------

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value.

        Args:
            key: Configuration key (supports dot notation, e.g., "api.key")
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        keys = key.split(".")
        value = self._config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default

        return value if value is not None else default

    def require_config(self, key: str) -> Any:
        """
        Get a required configuration value.

        Args:
            key: Configuration key

        Returns:
            Configuration value

        Raises:
            ValueError: If key is not found
        """
        value = self.get_config(key)
        if value is None:
            raise ValueError(f"Required configuration key '{key}' not found for plugin {self._plugin_id}")
        return value

    def set_config(self, key: str, value: Any) -> None:
        """
        Set a configuration value.

        Args:
            key: Configuration key (supports dot notation, e.g., "api.key")
            value: Value to set
        """
        keys = key.split(".")
        config = self._config

        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        config[keys[-1]] = value
        self._log.debug(f"Set config {key} = {value}")

    # -------------------------------------------------------------------------
    # Registration APIs
    # -------------------------------------------------------------------------

    def register_source(self, source_id: str, source_adapter: Any) -> bool:
        """
        Register a source adapter.

        Args:
            source_id: Unique identifier for the source
            source_adapter: SourceAdapter instance or class

        Returns:
            True if registration succeeded
        """
        if self._source_registry is None:
            self._log.warning("Source registry not available")
            return False

        try:
            self._source_registry.register(source_id, source_adapter)
            self._log.info(f"Registered source: {source_id}")
            return True
        except Exception as exc:
            self._log.error(f"Failed to register source {source_id}: {exc}")
            return False

    def register_delivery(self, delivery_id: str, delivery_handler: Any) -> bool:
        """
        Register a delivery handler.

        Args:
            delivery_id: Unique identifier for the delivery
            delivery_handler: DeliveryHandler instance or class

        Returns:
            True if registration succeeded
        """
        if self._delivery_registry is None:
            self._log.warning("Delivery registry not available")
            return False

        try:
            self._delivery_registry.register(delivery_id, delivery_handler)
            self._log.info(f"Registered delivery: {delivery_id}")
            return True
        except Exception as exc:
            self._log.error(f"Failed to register delivery {delivery_id}: {exc}")
            return False

    def register_agent(self, agent_id: str, agent_adapter: Any) -> bool:
        """
        Register an agent adapter.

        Args:
            agent_id: Unique identifier for the agent
            agent_adapter: AgentAdapter instance or class

        Returns:
            True if registration succeeded
        """
        if self._agent_registry is None:
            self._log.warning("Agent registry not available")
            return False

        try:
            self._agent_registry.register(agent_id, agent_adapter)
            self._log.info(f"Registered agent: {agent_id}")
            return True
        except Exception as exc:
            self._log.error(f"Failed to register agent {agent_id}: {exc}")
            return False

    # -------------------------------------------------------------------------
    # Event Publishing Aliases
    # -------------------------------------------------------------------------

    def emit_event(self, event: Any) -> None:
        """
        Emit an event to the event bus.

        Alias for publish_event().

        Args:
            event: EventEnvelope to emit
        """
        return self.publish_event(event)

    def get_event_bus(self) -> "EventBus | None":
        """
        Get the event bus instance.

        Returns:
            EventBus instance or None if not available
        """
        return self._event_bus
