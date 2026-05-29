"""
Hot reload mechanism for plugins.

Provides automatic plugin reloading when files change, similar to AstrBot's
ASTRBOT_RELOAD mechanism. Uses file watching to detect changes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class HotReloader:
    """
    Hot reload manager for plugins.

    Monitors plugin files and triggers reload when changes are detected.

    Features:
    - File system watching
    - Debounced reloads (avoid rapid reloading)
    - Configurable watch patterns
    - Event callbacks

    Example:
        reloader = HotReloader("./plugins")
        reloader.on_reload(lambda plugin_id: print(f"Reloaded: {plugin_id}"))
        await reloader.start()
    """

    def __init__(
        self,
        plugins_dir: str = "./plugins",
        watch_patterns: list[str] | None = None,
        debounce_seconds: float = 1.0,
    ):
        """
        Initialize the hot reloader.

        Args:
            plugins_dir: Directory to watch for changes
            watch_patterns: Glob patterns for files to watch (default: ["*.py"])
            debounce_seconds: Seconds to wait before reloading after changes
        """
        self._plugins_dir = Path(plugins_dir)
        self._watch_patterns = watch_patterns or ["*.py", "*.json", "*.yaml"]
        self._debounce_seconds = debounce_seconds

        self._running = False
        self._reload_callbacks: list[Callable[[str], None]] = []
        self._watched_files: dict[str, float] = {}  # path -> mtime
        self._pending_reloads: dict[str, float] = {}  # plugin_id -> trigger_time
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._task: asyncio.Task | None = None

    @property
    def plugins_dir(self) -> Path:
        """The plugins directory being watched."""
        return self._plugins_dir

    def on_reload(self, callback: Callable[[str], None]) -> None:
        """
        Register a callback for reload events.

        Args:
            callback: Function that takes (plugin_id) as argument
        """
        self._reload_callbacks.append(callback)

    async def start(self) -> None:
        """Start watching for file changes."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info("HotReloader started")

    async def stop(self) -> None:
        """Stop watching for file changes."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("HotReloader stopped")

    async def _watch_loop(self) -> None:
        """
        Main watch loop.

        Scans files periodically and triggers reloads for changed files.
        """
        # Initial scan
        await self._scan_files()

        while self._running:
            try:
                await asyncio.sleep(1.0)  # Check every second

                # Scan for changes
                changed = await self._check_changes()

                # Process pending reloads (debounced)
                now = time.time()
                for plugin_id, trigger_time in list(self._pending_reloads.items()):
                    if now >= trigger_time:
                        await self._trigger_reload(plugin_id)
                        del self._pending_reloads[plugin_id]

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in watch loop: {exc}")

    async def _scan_files(self) -> None:
        """Scan plugins directory and record file mtimes."""
        if not self._plugins_dir.exists():
            return

        for pattern in self._watch_patterns:
            for path in self._plugins_dir.rglob(pattern):
                if path.is_file():
                    try:
                        mtime = path.stat().st_mtime
                        self._watched_files[str(path)] = mtime
                    except OSError:
                        pass

    async def _check_changes(self) -> list[tuple[str, str]]:
        """
        Check for file changes.

        Returns:
            List of (plugin_id, file_path) tuples for changed files
        """
        changed = []

        for pattern in self._watch_patterns:
            for path in self._plugins_dir.rglob(pattern):
                if not path.is_file():
                    continue

                path_str = str(path)
                try:
                    current_mtime = path.stat().st_mtime
                    previous_mtime = self._watched_files.get(path_str)

                    if previous_mtime is None:
                        # New file
                        self._watched_files[path_str] = current_mtime
                        changed.append((self._path_to_plugin_id(path), path_str))

                    elif current_mtime > previous_mtime:
                        # Modified file
                        self._watched_files[path_str] = current_mtime
                        plugin_id = self._path_to_plugin_id(path)
                        changed.append((plugin_id, path_str))
                        self._schedule_reload(plugin_id)

                except OSError:
                    pass

        return changed

    def _path_to_plugin_id(self, path: Path) -> str:
        """
        Convert a file path to a plugin ID.

        Assumes plugins are organized with each plugin in its own directory,
        and the directory name is the plugin ID.
        """
        try:
            relative = path.relative_to(self._plugins_dir)
            parts = relative.parts

            if len(parts) >= 2:
                # plugin_dir/file.py -> plugin_dir
                return parts[0]
            elif len(parts) == 1:
                # file.py (root level) -> use parent dir
                return self._plugins_dir.name
            else:
                return "unknown"

        except ValueError:
            return "unknown"

    def _schedule_reload(self, plugin_id: str) -> None:
        """
        Schedule a reload for a plugin after debounce delay.
        """
        trigger_time = time.time() + self._debounce_seconds
        self._pending_reloads[plugin_id] = trigger_time
        logger.debug(f"Scheduled reload for {plugin_id} in {self._debounce_seconds}s")

    async def _trigger_reload(self, plugin_id: str) -> None:
        """
        Trigger a reload for a plugin.
        """
        logger.info(f"Hot reloading plugin: {plugin_id}")

        for callback in self._reload_callbacks:
            try:
                callback(plugin_id)
            except Exception as exc:
                logger.error(f"Error in reload callback for {plugin_id}: {exc}")

    async def force_reload(self, plugin_id: str) -> None:
        """
        Force an immediate reload for a plugin (bypasses debounce).
        """
        logger.info(f"Force reloading plugin: {plugin_id}")
        await self._trigger_reload(plugin_id)

    def get_watched_plugins(self) -> list[str]:
        """Get list of plugin IDs currently being watched."""
        seen = set()
        plugins = []

        for path_str in self._watched_files.keys():
            path = Path(path_str)
            plugin_id = self._path_to_plugin_id(path)
            if plugin_id not in seen:
                seen.add(plugin_id)
                plugins.append(plugin_id)

        return plugins


# ============================================================================
# Integration Helper
# ============================================================================

class HotReloadMixin:
    """
    Mixin for PluginRegistry to add hot reload support.

    Example:
        class HotReloadRegistry(HotReloadMixin, PluginRegistry):
            pass

        registry = HotReloadRegistry()
        await registry.start_hot_reload()
    """

    def __init__(self, *args, hot_reload_dir: str = "./plugins", **kwargs):
        super().__init__(*args, **kwargs)
        self._hot_reloader = HotReloader(plugins_dir=hot_reload_dir)

    async def start_hot_reload(self) -> None:
        """Start hot reload monitoring."""
        self._hot_reloader.on_reload(self._on_hot_reload)
        await self._hot_reloader.start()

    async def stop_hot_reload(self) -> None:
        """Stop hot reload monitoring."""
        await self._hot_reloader.stop()

    async def _on_hot_reload(self, plugin_id: str) -> None:
        """
        Handle hot reload event.

        Override this method to implement custom reload logic.
        """
        logger.info(f"Hot reload triggered for: {plugin_id}")

        # Terminate existing instance
        await self.terminate_plugin(plugin_id)

        # Reload the plugin module
        result = await self.reload_plugin(plugin_id)

        if result.success:
            # Re-initialize
            await self.initialize_plugin(plugin_id)
            logger.info(f"Hot reload completed for: {plugin_id}")
        else:
            logger.error(f"Hot reload failed for {plugin_id}: {result.error}")
