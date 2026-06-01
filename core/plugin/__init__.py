"""
PulseRelay Plugin System.

Provides a plugin architecture for extending PulseRelay with:
- SourceAdapters: Event source plugins (WeFlow, Telegram, Slack, etc.)
- DeliveryHandlers: Event delivery plugins (Bark, Webhook, Email, etc.)
- AgentAdapters: AI agent runtime plugins (OpenClaw, OpenAI, Claude, etc.)
- Routers: Custom routing logic plugins
- Policies: Custom policy/approval plugins

Based on AstrBot Star system patterns with __init_subclass__ auto-registration.
"""

from core.plugin.manifest import (
    PluginManifest,
    PluginMetadata,
    PluginCapabilities,
    PluginState,
    PluginEntryPoint,
)
from core.plugin.registry import PluginRegistry
from core.plugin.base import Plugin
from core.plugin.context import PluginContext
from core.plugin.loader import PluginLoader, PluginLoadSpec
from core.plugin.sandbox import PluginSandbox, SandboxedPluginRunner, create_default_sandbox
from core.plugin.hot_reload import HotReloader, HotReloadMixin

__all__ = [
    # Manifest
    "PluginManifest",
    "PluginMetadata",
    "PluginCapabilities",
    "PluginState",
    "PluginEntryPoint",
    # Registry
    "PluginRegistry",
    # Base
    "Plugin",
    # Context
    "PluginContext",
    # Loader
    "PluginLoader",
    "PluginLoadSpec",
    # Sandbox
    "PluginSandbox",
    "SandboxedPluginRunner",
    "create_default_sandbox",
    # Hot Reload
    "HotReloader",
    "HotReloadMixin",
]
