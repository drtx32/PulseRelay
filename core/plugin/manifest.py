"""
Plugin manifest and metadata definitions.

Defines the PluginManifest dataclass which serves as the plugin's identity card,
similar to AstrBot's StarMetadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


class PluginState(Enum):
    """Lifecycle states of a plugin."""

    DISCOVERED = "discovered"  # Found but not loaded
    LOADING = "loading"  # Currently being loaded
    LOADED = "loaded"  # Successfully loaded
    INITIALIZING = "initializing"  # initialize() in progress
    ACTIVE = "active"  # Fully operational
    TERMINATING = "terminating"  # terminate() in progress
    INACTIVE = "inactive"  # Disabled or failed
    ERROR = "error"  # Failed with error


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


@dataclass
class PluginCapabilities:
    """
    Capabilities exposed by a plugin.

    Plugins declare what they support to enable capability-based discovery.
    """

    # General capabilities
    supports_hot_reload: bool = False  # Can be reloaded without restart
    supports_configuration: bool = False  # Has custom configuration schema
    supports_webhook: bool = False  # Exposes webhook endpoints

    # Source-specific capabilities
    supports_websocket: bool = False
    supports_streaming: bool = False
    supports_polling: bool = False

    # Delivery-specific capabilities
    supports_text: bool = False
    supports_markdown: bool = False
    supports_html: bool = False
    supports_files: bool = False
    supports_streaming_response: bool = False

    # Agent-specific capabilities
    supports_function_calling: bool = False


@dataclass
class PluginMetadata:
    """
    Static metadata describing a plugin.

    Similar to AstrBot's StarMetadata, this is the identity card for plugins.
    Plugins must provide this as a class attribute.
    """

    id: str  # Unique identifier (e.g., "telegram-source", "slack-delivery")
    name: str  # Human-readable name (e.g., "Telegram Source")
    version: str = "0.1.0"  # Semantic version
    description: str = ""  # One-line description
    author: str = ""  # Plugin author
    repo: str = ""  # GitHub repository URL
    tags: list[str] = field(default_factory=list)  # Searchable tags
    capabilities: PluginCapabilities = field(default_factory=PluginCapabilities)

    # Compatibility
    pulse_relay_version: str = ">=0.1.0"  # Compatible PulseRelay version range
    astrbot_version: str = ""  # AstrBot version (for AstrBot plugins)

    # Plugin classification
    entry_points: list[PluginEntryPoint] = field(default_factory=list)

    # Special flags
    reserved: bool = False  # Built-in plugins cannot be uninstalled
    premium: bool = False  # Premium/paid plugin

    # Dependencies
    dependencies: list[str] = field(default_factory=list)  # Plugin IDs this depends on

    # Configuration schema (JSON Schema string)
    config_schema: str = ""  # JSON Schema for plugin configuration


@dataclass
class PluginManifest:
    """
    Complete manifest for a plugin, including metadata and runtime state.

    This is the full plugin identity that includes both static metadata
    (PluginMetadata) and runtime state information.
    """

    metadata: PluginMetadata
    state: PluginState = PluginState.DISCOVERED
    enabled: bool = True
    error_message: str = ""

    # Timestamps
    discovered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    loaded_at: str = ""
    initialized_at: str = ""
    last_error_at: str = ""

    # Runtime info
    instance: "Plugin | None" = None  # The plugin instance
    entry_point: PluginEntryPoint | None = None  # Primary entry point

    # Source adapter reference (for SOURCE entry point)
    source_adapter_class: type | None = None

    # Delivery handler reference (for DELIVERY entry point)
    delivery_handler_class: type | None = None

    # Agent adapter reference (for AGENT entry point)
    agent_adapter_class: type | None = None

    # Router reference (for ROUTER entry point)
    router_class: type | None = None

    # Policy reference (for POLICY entry point)
    policy_class: type | None = None

    def __hash__(self) -> int:
        return hash(self.metadata.id)

    @property
    def plugin_id(self) -> str:
        return self.metadata.id

    @property
    def is_operational(self) -> bool:
        return self.state == PluginState.ACTIVE and self.enabled


@dataclass
class PluginLoadResult:
    """Result of a plugin load operation."""

    success: bool
    manifest: PluginManifest | None = None
    error: str = ""
    loaded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class PluginInitResult:
    """Result of a plugin initialize operation."""

    success: bool
    plugin_id: str
    error: str = ""
    initialized_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class PluginTerminateResult:
    """Result of a plugin terminate operation."""

    success: bool
    plugin_id: str
    error: str = ""
    terminated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
