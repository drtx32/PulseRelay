"""
Plugin base class with lifecycle and auto-registration.

Inspired by AstrBot's Star class, PulseRelay Plugin uses __init_subclass__
for automatic registration into the PluginRegistry.

Example:
    class MySourcePlugin(Plugin, entry_point=PluginEntryPoint.SOURCE):
        metadata = PluginMetadata(
            id="my-source",
            name="My Source",
            version="1.0.0",
        )

    # Auto-registers with PluginRegistry on class definition
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from core.plugin.manifest import (
    PluginEntryPoint,
    PluginManifest,
    PluginMetadata,
    PluginState,
)

if TYPE_CHECKING:
    from core.plugin.registry import PluginRegistry

logger = logging.getLogger(__name__)


class Plugin(ABC):
    """
    Base class for all PulseRelay plugins.

    Plugins inherit from this class and specify their entry_point type.
    Auto-registration occurs via __init_subclass__ when the class is defined.

    Lifecycle methods:
        - initialize(): Called when plugin is activated
        - terminate(): Called when plugin is deactivated

    Example:
        class TelegramSource(Plugin, entry_point=PluginEntryPoint.SOURCE):
            metadata = PluginMetadata(
                id="telegram-source",
                name="Telegram Source",
                version="1.0.0",
                description="Telegram message source adapter",
                entry_points=[PluginEntryPoint.SOURCE],
            )

            def initialize(self) -> None:
                logger.info(f"Initializing {self.metadata.name}")

            def terminate(self) -> None:
                logger.info(f"Terminating {self.metadata.name}")
    """

    # Class-level metadata (must be overridden)
    metadata: ClassVar[PluginMetadata]

    # Class-level entry point (must be set by subclass)
    entry_point: ClassVar[PluginEntryPoint]

    # Internal state
    _registry: ClassVar["PluginRegistry | None"] = None
    _manifest: PluginManifest | None = None

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._state = PluginState.DISCOVERED

    @property
    def plugin_id(self) -> str:
        """Return the plugin's unique identifier."""
        return self.metadata.id

    @property
    def state(self) -> PluginState:
        """Return the current plugin state."""
        return self._state

    @state.setter
    def state(self, value: PluginState) -> None:
        """Set the plugin state and update manifest if registered."""
        self._state = value
        if self._manifest is not None:
            self._manifest.state = value

    async def initialize(self) -> None:
        """
        Initialize the plugin.

        Called when the plugin is activated. Subclasses should override
        this method to set up resources, connections, etc.

        Override in subclasses. Default implementation is a no-op.
        """
        pass

    async def terminate(self) -> None:
        """
        Terminate the plugin.

        Called when the plugin is deactivated. Subclasses should override
        this method to clean up resources, close connections, etc.

        Override in subclasses. Default implementation is a no-op.
        """
        pass

    def get_manifest(self) -> PluginManifest:
        """Return the plugin's manifest."""
        if self._manifest is None:
            self._manifest = PluginManifest(
                metadata=self.metadata,
                state=self._state,
                enabled=True,
                entry_point=self.entry_point,
            )
        return self._manifest

    # --- Auto-registration via __init_subclass__ ---

    def __init_subclass__(cls, entry_point: PluginEntryPoint | None = None, **kwargs: Any) -> None:
        """
        Auto-register plugin with the global PluginRegistry.

        When a subclass is defined (not instantiated), it automatically
        registers itself with the registry if one is set.
        """
        super().__init_subclass__(**kwargs)

        # Skip registration for intermediate base classes (those without metadata)
        # These are mixin classes like SourcePlugin, DeliveryPlugin, etc.
        if not hasattr(cls, "metadata") or cls.metadata is None:
            # If entry_point was explicitly provided, set it for inheritance
            if entry_point is not None:
                cls.entry_point = entry_point
            # Don't raise error - this is an intermediate base class
            return

        # Validate that metadata is defined
        if not hasattr(cls, "metadata") or cls.metadata is None:
            raise TypeError(
                f"Plugin {cls.__name__} must define 'metadata' class attribute as PluginMetadata"
            )

        # Validate entry_point
        if entry_point is not None:
            cls.entry_point = entry_point
        elif not hasattr(cls, "entry_point") or cls.entry_point is None:
            # Try to infer from metadata
            if cls.metadata.entry_points:
                cls.entry_point = cls.metadata.entry_points[0]
            else:
                raise TypeError(
                    f"Plugin {cls.__name__} must specify entry_point via parameter or metadata"
                )

        # Register with global registry if set
        if Plugin._registry is not None:
            Plugin._registry._register_plugin_class(cls)

        logger.debug(f"Discovered plugin: {cls.metadata.id} ({cls.entry_point.value})")

    @classmethod
    def set_registry(cls, registry: "PluginRegistry | None") -> None:
        """
        Set the global PluginRegistry for auto-registration.

        Called by PluginRegistry on initialization.
        """
        cls._registry = registry


# ============================================================================
# Source Plugin Mixin
# ============================================================================


class SourcePlugin(Plugin, entry_point=PluginEntryPoint.SOURCE):
    """
    Base class for SourceAdapter plugins.

    Source plugins receive events from external systems and normalize
    them into EventEnvelope objects.

    Example:
        class TelegramSourcePlugin(SourcePlugin):
            metadata = PluginMetadata(
                id="telegram-source",
                name="Telegram Source",
                version="1.0.0",
                description="Receive messages from Telegram",
                entry_points=[PluginEntryPoint.SOURCE],
                capabilities=PluginCapabilities(supports_websocket=True),
            )
    """

    async def run(self) -> None:
        """
        Main event loop for the source adapter.

        Subclasses must implement this method to define how events
        are received from the external system.
        """
        raise NotImplementedError


# ============================================================================
# Delivery Plugin Mixin
# ============================================================================


class DeliveryPlugin(Plugin, entry_point=PluginEntryPoint.DELIVERY):
    """
    Base class for DeliveryHandler plugins.

    Delivery plugins send normalized messages to external systems.

    Example:
        class SlackDeliveryPlugin(DeliveryPlugin):
            metadata = PluginMetadata(
                id="slack-delivery",
                name="Slack Delivery",
                version="1.0.0",
                description="Send notifications to Slack",
                entry_points=[PluginEntryPoint.DELIVERY],
                capabilities=PluginCapabilities(supports_markdown=True),
            )
    """

    async def deliver(self, message: Any, event: Any = None) -> Any:
        """
        Deliver a message to the external system.

        Subclasses must implement this method.
        """
        raise NotImplementedError


# ============================================================================
# Agent Plugin Mixin
# ============================================================================


class AgentPlugin(Plugin, entry_point=PluginEntryPoint.AGENT):
    """
    Base class for AgentAdapter plugins.

    Agent plugins connect to AI agent runtimes.

    Example:
        class ClaudeAgentPlugin(AgentPlugin):
            metadata = PluginMetadata(
                id="claude-agent",
                name="Claude Agent",
                version="1.0.0",
                description="Connect to Claude Code runtime",
                entry_points=[PluginEntryPoint.AGENT],
                capabilities=PluginCapabilities(supports_streaming=True),
            )
    """

    async def send_request(self, request: Any) -> Any:
        """
        Send a request to the agent runtime.

        Subclasses must implement this method.
        """
        raise NotImplementedError


# ============================================================================
# Router Plugin Mixin
# ============================================================================


class RouterPlugin(Plugin, entry_point=PluginEntryPoint.ROUTER):
    """
    Base class for Router plugins.

    Router plugins implement custom routing logic.

    Example:
        class CustomRouterPlugin(RouterPlugin):
            metadata = PluginMetadata(
                id="custom-router",
                name="Custom Router",
                version="1.0.0",
                description="Custom routing logic",
                entry_points=[PluginEntryPoint.ROUTER],
            )
    """

    def route(self, event: Any) -> Any:
        """
        Route an event and produce a routing decision.

        Subclasses must implement this method.
        """
        raise NotImplementedError


# ============================================================================
# Policy Plugin Mixin
# ============================================================================


class PolicyPlugin(Plugin, entry_point=PluginEntryPoint.POLICY):
    """
    Base class for PolicyEngine plugins.

    Policy plugins implement custom policy evaluation logic.

    Example:
        class CustomPolicyPlugin(PolicyPlugin):
            metadata = PluginMetadata(
                id="custom-policy",
                name="Custom Policy",
                version="1.0.0",
                description="Custom approval policy",
                entry_points=[PluginEntryPoint.POLICY],
            )
    """

    def evaluate(self, event: Any, context: Any = None) -> Any:
        """
        Evaluate policy for an event.

        Subclasses must implement this method.
        """
        raise NotImplementedError
