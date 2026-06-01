"""
Plugin registry for managing plugin lifecycle.

The PluginRegistry serves as the central manager for all plugins,
handling discovery, loading, initialization, and lifecycle management.

Based on AstrBot's star_manager.py and star_handler.py patterns.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.plugin.manifest import (
    PluginEntryPoint,
    PluginInitResult,
    PluginLoadResult,
    PluginManifest,
    PluginState,
    PluginTerminateResult,
)

logger = logging.getLogger(__name__)


class PluginRegistry:
    """
    Central registry for PulseRelay plugins.

    Responsibilities:
    - Plugin discovery (auto-registration via __init_subclass__)
    - Plugin loading (import modules, instantiate classes)
    - Plugin initialization (call initialize() lifecycle method)
    - Plugin termination (call terminate() lifecycle method)
    - State management
    - Hot reload support

    Example:
        registry = PluginRegistry()
        registry.discover_plugins("./plugins")
        await registry.initialize_all()
    """

    def __init__(self, plugins_dir: str = "./plugins"):
        self._plugins_dir = Path(plugins_dir)
        self._manifests: dict[str, PluginManifest] = {}  # plugin_id -> manifest
        self._instances: dict[str, Any] = {}  # plugin_id -> instance
        self._plugin_classes: dict[str, type] = {}  # plugin_id -> class
        self._plugin_modules: dict[str, str] = {}  # plugin_id -> module name
        self._entry_points: dict[PluginEntryPoint, list[str]] = {
            ep: [] for ep in PluginEntryPoint
        }

        # Callbacks for plugin lifecycle events
        self._on_plugin_loaded: list[Callable[[PluginManifest], None]] = []
        self._on_plugin_initialized: list[Callable[[str], None]] = []
        self._on_plugin_error: list[Callable[[str, str], None]] = []

        # Async executor for blocking plugin operations
        self._executor = ThreadPoolExecutor(max_workers=4)

        # Set global registry for auto-registration
        from core.plugin.base import Plugin

        Plugin.set_registry(self)

        logger.info("PluginRegistry initialized")

    # -------------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------------

    def discover_plugins(self, plugins_dir: str | None = None) -> list[PluginLoadResult]:
        """
        Discover plugins in the specified directory.

        Scans for Python modules with a valid Plugin subclass and loads them.
        """
        plugins_dir = Path(plugins_dir) if plugins_dir else self._plugins_dir

        if not plugins_dir.exists():
            logger.warning(f"Plugins directory does not exist: {plugins_dir}")
            return []

        results: list[PluginLoadResult] = []

        # Add plugins directory to sys.path for imports
        plugins_dir_str = str(plugins_dir)
        if plugins_dir_str not in sys.path:
            sys.path.insert(0, plugins_dir_str)

        # Discover plugin modules
        for item in plugins_dir.iterdir():
            if not item.is_dir() and item.suffix != ".py":
                continue

            if item.is_dir() and not (item / "__init__.py").exists():
                continue

            module_name = item.stem if item.suffix == ".py" else item.name
            if module_name == "__init__":
                continue

            try:
                result = self._load_plugin_module(module_name)
                results.append(result)
            except Exception as exc:
                logger.error(f"Failed to load plugin {module_name}: {exc}")
                results.append(
                    PluginLoadResult(
                        success=False,
                        error=str(exc),
                    )
                )

        return results

    def _load_plugin_module(self, module_name: str) -> PluginLoadResult:
        """Load a plugin module and register its classes."""
        try:
            from core.plugin.base import Plugin

            # Import the module
            module = importlib.import_module(module_name)

            # Find all Plugin subclasses in the module
            discovered: list[type] = []
            for attr_name in dir(module):
                attr = getattr(module, attr_name)

                # Check if it's a Plugin subclass (not Plugin itself)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Plugin)
                    and attr is not Plugin
                    and hasattr(attr, "metadata")
                    and hasattr(attr, "entry_point")
                ):
                    discovered.append(attr)

            if not discovered:
                return PluginLoadResult(
                    success=True,
                    error="No plugin classes found in module",
                )

            # Register each discovered plugin class
            for plugin_cls in discovered:
                self._register_plugin_class(plugin_cls, module_name=module_name)

            return PluginLoadResult(
                success=True,
                manifest=self._manifests.get(discovered[-1].metadata.id),
            )

        except Exception as exc:
            return PluginLoadResult(
                success=False,
                error=f"Import error: {str(exc)}",
            )

    def _register_plugin_class(self, plugin_cls: type, module_name: str | None = None) -> None:
        """
        Register a plugin class discovered via __init_subclass__ or explicit import.

        This is called automatically by Plugin.__init_subclass__ and also
        during explicit plugin loading.
        """
        plugin_id = plugin_cls.metadata.id

        self._plugin_classes[plugin_id] = plugin_cls
        if module_name:
            self._plugin_modules[plugin_id] = module_name

        if plugin_id in self._manifests:
            logger.debug(f"Plugin {plugin_id} already registered")
            return

        manifest = PluginManifest(
            metadata=plugin_cls.metadata,
            state=PluginState.DISCOVERED,
            enabled=not plugin_cls.metadata.reserved,  # Reserved plugins start disabled
            entry_point=plugin_cls.entry_point,
        )

        self._manifests[plugin_id] = manifest
        self._entry_points[plugin_cls.entry_point].append(plugin_id)

        logger.info(f"Discovered plugin: {plugin_id} ({plugin_cls.entry_point.value})")
        self._notify_loaded(manifest)

    def discover_entry_points(self) -> dict[PluginEntryPoint, list[str]]:
        """
        Return all registered plugins grouped by entry point.

        Returns:
            Dict mapping entry point type to list of plugin IDs
        """
        return {ep: list(ids) for ep, ids in self._entry_points.items()}

    # -------------------------------------------------------------------------
    # Plugin Instance Management
    # -------------------------------------------------------------------------

    def get(self, plugin_id: str) -> PluginManifest | None:
        """Get a plugin manifest by ID."""
        return self._manifests.get(plugin_id)

    def get_instance(self, plugin_id: str) -> Any | None:
        """Get a plugin instance by ID."""
        return self._instances.get(plugin_id)

    def list(
        self,
        entry_point: PluginEntryPoint | None = None,
        state: PluginState | None = None,
        enabled: bool | None = None,
    ) -> list[PluginManifest]:
        """
        List plugins with optional filtering.

        Args:
            entry_point: Filter by entry point type
            state: Filter by state
            enabled: Filter by enabled flag
        """
        manifests = list(self._manifests.values())

        if entry_point is not None:
            manifests = [m for m in manifests if m.entry_point == entry_point]

        if state is not None:
            manifests = [m for m in manifests if m.state == state]

        if enabled is not None:
            manifests = [m for m in manifests if m.enabled == enabled]

        return manifests

    def list_by_entry_point(self, entry_point: PluginEntryPoint) -> list[PluginManifest]:
        """List all plugins for a specific entry point."""
        plugin_ids = self._entry_points.get(entry_point, [])
        return [self._manifests[pid] for pid in plugin_ids if pid in self._manifests]

    # -------------------------------------------------------------------------
    # Lifecycle Management
    # -------------------------------------------------------------------------

    async def initialize_plugin(self, plugin_id: str) -> PluginInitResult:
        """
        Initialize a single plugin (call its initialize() method).

        Creates an instance and calls the plugin's initialize() lifecycle method.
        """
        manifest = self._manifests.get(plugin_id)
        if manifest is None:
            return PluginInitResult(
                success=False,
                plugin_id=plugin_id,
                error="Plugin not found",
            )

        if manifest.state == PluginState.ACTIVE:
            return PluginInitResult(
                success=True,
                plugin_id=plugin_id,
                error="Already active",
            )

        try:
            # Update state
            manifest.state = PluginState.INITIALIZING

            # Import the plugin class and create instance
            from core.plugin.base import Plugin

            plugin_cls = self._get_plugin_class(manifest)
            if plugin_cls is None:
                raise RuntimeError(f"Cannot find plugin class for {plugin_id}")

            # Create instance with config
            instance = plugin_cls(config=self._get_plugin_config(plugin_id))
            manifest.instance = instance

            # Store instance
            self._instances[plugin_id] = instance

            # Call initialize (handle both sync and async)
            if asyncio.iscoroutinefunction(instance.initialize):
                await instance.initialize()
            else:
                # Run sync initialize in executor
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(self._executor, instance.initialize)

            # Update state
            manifest.state = PluginState.ACTIVE
            manifest.initialized_at = datetime.now(timezone.utc).isoformat()

            logger.info(f"Initialized plugin: {plugin_id}")
            self._notify_initialized(plugin_id)

            return PluginInitResult(success=True, plugin_id=plugin_id)

        except Exception as exc:
            manifest.state = PluginState.ERROR
            manifest.error_message = str(exc)
            manifest.last_error_at = datetime.now(timezone.utc).isoformat()

            logger.error(f"Failed to initialize plugin {plugin_id}: {exc}")
            self._notify_error(plugin_id, str(exc))

            return PluginInitResult(
                success=False,
                plugin_id=plugin_id,
                error=str(exc),
            )

    async def terminate_plugin(self, plugin_id: str) -> PluginTerminateResult:
        """
        Terminate a single plugin (call its terminate() method).

        Calls the plugin's terminate() lifecycle method and removes the instance.
        """
        manifest = self._manifests.get(plugin_id)
        if manifest is None:
            return PluginTerminateResult(
                success=False,
                plugin_id=plugin_id,
                error="Plugin not found",
            )

        instance = self._instances.get(plugin_id)
        if instance is None:
            return PluginTerminateResult(
                success=False,
                plugin_id=plugin_id,
                error="Plugin not initialized",
            )

        try:
            manifest.state = PluginState.TERMINATING

            # Call terminate (handle both sync and async)
            if asyncio.iscoroutinefunction(instance.terminate):
                await instance.terminate()
            else:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(self._executor, instance.terminate)

            # Update state
            manifest.state = PluginState.INACTIVE
            manifest.instance = None

            # Remove instance
            del self._instances[plugin_id]

            logger.info(f"Terminated plugin: {plugin_id}")

            return PluginTerminateResult(success=True, plugin_id=plugin_id)

        except Exception as exc:
            manifest.state = PluginState.ERROR
            manifest.error_message = str(exc)
            manifest.last_error_at = datetime.now(timezone.utc).isoformat()

            logger.error(f"Failed to terminate plugin {plugin_id}: {exc}")

            return PluginTerminateResult(
                success=False,
                plugin_id=plugin_id,
                error=str(exc),
            )

    async def initialize_all(self) -> list[PluginInitResult]:
        """Initialize all enabled plugins."""
        results = []
        for manifest in self._manifests.values():
            if manifest.enabled and manifest.state not in (
                PluginState.ACTIVE,
                PluginState.INITIALIZING,
            ):
                result = await self.initialize_plugin(manifest.plugin_id)
                results.append(result)
        return results

    async def terminate_all(self) -> list[PluginTerminateResult]:
        """Terminate all active plugins."""
        results = []
        for manifest in self._manifests.values():
            if manifest.state == PluginState.ACTIVE:
                result = await self.terminate_plugin(manifest.plugin_id)
                results.append(result)
        return results

    # -------------------------------------------------------------------------
    # Plugin Enable/Disable
    # -------------------------------------------------------------------------

    async def enable_plugin(self, plugin_id: str) -> bool:
        """Enable a plugin and initialize it if not already active."""
        manifest = self._manifests.get(plugin_id)
        if manifest is None:
            return False

        manifest.enabled = True

        if manifest.state != PluginState.ACTIVE:
            await self.initialize_plugin(plugin_id)

        return True

    async def disable_plugin(self, plugin_id: str) -> bool:
        """Disable a plugin and terminate it if active."""
        manifest = self._manifests.get(plugin_id)
        if manifest is None:
            return False

        manifest.enabled = False

        if manifest.state == PluginState.ACTIVE:
            await self.terminate_plugin(plugin_id)

        return True

    # -------------------------------------------------------------------------
    # Hot Reload
    # -------------------------------------------------------------------------

    async def reload_plugin(self, plugin_id: str) -> PluginLoadResult:
        """
        Hot reload a plugin.

        Terminates the plugin, re-imports the module, and re-initializes.
        """
        manifest = self._manifests.get(plugin_id)
        if manifest is None:
            return PluginLoadResult(
                success=False,
                error="Plugin not found",
            )

        # Terminate existing instance
        if manifest.state == PluginState.ACTIVE:
            await self.terminate_plugin(plugin_id)

        # Remove old manifest and instance
        old_entry_point = manifest.entry_point
        module_name = self._plugin_modules.get(plugin_id, plugin_id)
        del self._manifests[plugin_id]
        self._entry_points[old_entry_point].remove(plugin_id)
        if plugin_id in self._instances:
            del self._instances[plugin_id]
        self._plugin_classes.pop(plugin_id, None)
        self._plugin_modules.pop(plugin_id, None)

        try:
            # Remove from sys.modules to force re-import
            if module_name in sys.modules:
                del sys.modules[module_name]

            # Re-discover
            result = self._load_plugin_module(module_name)

            if result.success and result.manifest:
                result.manifest.enabled = manifest.enabled
                return result
            else:
                return PluginLoadResult(
                    success=False,
                    error=result.error or "Failed to reload",
                )

        except Exception as exc:
            return PluginLoadResult(
                success=False,
                error=f"Reload failed: {str(exc)}",
            )

    # -------------------------------------------------------------------------
    # Integration with Existing Registries
    # -------------------------------------------------------------------------

    def register_with_source_registry(self, source_registry: Any) -> None:
        """
        Register SOURCE entry point plugins with SourceRegistry.

        Iterates through all SOURCE plugins and registers their adapter
        classes with the provided SourceRegistry.
        """
        for manifest in self.list_by_entry_point(PluginEntryPoint.SOURCE):
            if manifest.state == PluginState.ACTIVE and manifest.instance:
                source_registry.register(manifest.instance)

    def register_with_delivery_registry(self, delivery_registry: Any) -> None:
        """
        Register DELIVERY entry point plugins with DeliveryRegistry.

        Iterates through all DELIVERY plugins and registers their handler
        classes with the provided DeliveryRegistry.
        """
        for manifest in self.list_by_entry_point(PluginEntryPoint.DELIVERY):
            if manifest.state == PluginState.ACTIVE and manifest.instance:
                delivery_registry.register(manifest.instance)

    def register_with_agent_registry(self, agent_registry: Any) -> None:
        """
        Register AGENT entry point plugins with AgentRegistry.

        Iterates through all AGENT plugins and registers their adapter
        classes with the provided AgentRegistry.
        """
        for manifest in self.list_by_entry_point(PluginEntryPoint.AGENT):
            if manifest.state == PluginState.ACTIVE and manifest.instance:
                agent_registry.register(manifest.instance)

    # -------------------------------------------------------------------------
    # Event Callbacks
    # -------------------------------------------------------------------------

    def on_plugin_loaded(self, callback: Callable[[PluginManifest], None]) -> None:
        """Register a callback for plugin loaded events."""
        self._on_plugin_loaded.append(callback)

    def on_plugin_initialized(self, callback: Callable[[str], None]) -> None:
        """Register a callback for plugin initialized events."""
        self._on_plugin_initialized.append(callback)

    def on_plugin_error(self, callback: Callable[[str, str], None]) -> None:
        """Register a callback for plugin error events."""
        self._on_plugin_error.append(callback)

    def _notify_initialized(self, plugin_id: str) -> None:
        for callback in self._on_plugin_initialized:
            try:
                callback(plugin_id)
            except Exception as exc:
                logger.error(f"Error in plugin initialized callback: {exc}")

    def _notify_loaded(self, manifest: PluginManifest) -> None:
        for callback in self._on_plugin_loaded:
            try:
                callback(manifest)
            except Exception as exc:
                logger.error(f"Error in plugin loaded callback: {exc}")

    def _notify_error(self, plugin_id: str, error: str) -> None:
        for callback in self._on_plugin_error:
            try:
                callback(plugin_id, error)
            except Exception as exc:
                logger.error(f"Error in plugin error callback: {exc}")

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _get_plugin_class(self, manifest: PluginManifest) -> type | None:
        """Get the plugin class for a manifest."""
        plugin_id = manifest.plugin_id
        plugin_cls = self._plugin_classes.get(plugin_id)
        if plugin_cls is not None:
            return plugin_cls

        module_name = self._plugin_modules.get(plugin_id)
        if module_name:
            self._load_plugin_module(module_name)
            return self._plugin_classes.get(plugin_id)

        return None

    def _get_plugin_config(self, plugin_id: str) -> dict[str, Any]:
        """Get configuration for a plugin (placeholder for config system)."""
        # TODO: Integrate with ConfigLoader
        return {}

    def health_snapshot(self) -> dict[str, Any]:
        """Get a snapshot of all plugin states."""
        return {
            plugin_id: {
                "state": manifest.state.value,
                "enabled": manifest.enabled,
                "entry_point": manifest.entry_point.value if manifest.entry_point else None,
                "error_message": manifest.error_message,
                "initialized_at": manifest.initialized_at,
                "last_error_at": manifest.last_error_at,
            }
            for plugin_id, manifest in self._manifests.items()
        }

    def shutdown(self) -> None:
        """Shutdown the registry and clean up resources."""
        # Cancel any pending operations
        self._executor.shutdown(wait=False)

        # Clear registry reference
        from core.plugin.base import Plugin

        Plugin.set_registry(None)
        self._plugin_classes.clear()
        self._plugin_modules.clear()

        logger.info("PluginRegistry shutdown")
