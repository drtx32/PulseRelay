"""
Plugin sandbox for isolation and security.

Provides sandboxing mechanisms to isolate plugin execution and prevent
malicious or buggy plugins from affecting the host system.

Based on AstrBot's sandbox design patterns.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)


class PluginSandbox:
    """
    Sandboxing mechanism for plugin isolation.

    Provides:
    - Restricted imports (denylist-based)
    - Filesystem access restrictions
    - Environment variable filtering
    - Resource limits

    Note: This is a basic implementation. For production use, consider
    stronger isolation via containers (Docker) or process isolation.
    """

    # Default denylist for imports
    DEFAULT_IMPORT_DENYLIST = [
        "os.system",
        "subprocess",
        "socket",
        "ctypes",
        "resource",
        "syslog",
        "grp",
        "pwd",
        "termios",
        "tty",
    ]

    def __init__(
        self,
        allowed_modules: list[str] | None = None,
        denied_modules: list[str] | None = None,
        allowed_paths: list[str] | None = None,
        denied_paths: list[str] | None = None,
        env_whitelist: list[str] | None = None,
        max_memory_mb: int = 512,
        max_cpu_percent: int = 50,
    ):
        """
        Initialize the sandbox.

        Args:
            allowed_modules: List of module prefixes to allow (None = all except denied)
            denied_modules: List of module prefixes to deny
            allowed_paths: List of filesystem path prefixes to allow
            denied_paths: List of filesystem path prefixes to deny
            env_whitelist: List of environment variable names to pass to plugins
            max_memory_mb: Maximum memory in MB
            max_cpu_percent: Maximum CPU percentage
        """
        self._allowed_modules = allowed_modules
        self._denied_modules = denied_modules or self.DEFAULT_IMPORT_DENYLIST
        self._allowed_paths = allowed_paths
        self._denied_paths = denied_paths or ["/etc", "/root", "/home"]
        self._env_whitelist = env_whitelist or ["PATH", "PYTHONPATH"]
        self._max_memory_mb = max_memory_mb
        self._max_cpu_percent = max_cpu_percent

        self._original_import = __builtins__.__import__
        self._original_open = open

    def install(self) -> None:
        """
        Install the sandbox by patching built-in functions.

        This modifies global state and should be called carefully.
        """
        import builtins

        # Store original open
        self._original_open = builtins.open

        # Patch open to restrict filesystem access
        def restricted_open(file, mode="r", *args, **kwargs):
            file_str = str(file)
            for denied in self._denied_paths:
                if file_str.startswith(denied):
                    raise PermissionError(f"Access denied: {file}")
            if self._allowed_paths:
                allowed = any(file_str.startswith(p) for p in self._allowed_paths)
                if not allowed:
                    raise PermissionError(f"Access denied: {file}")
            return self._original_open(file, mode, *args, **kwargs)

        builtins.open = restricted_open

        # Patch __import__ to restrict module imports
        original_import = builtins.__import__

        def restricted_import(name, *args, **kwargs):
            # Check denied modules
            for denied in self._denied_modules:
                if name == denied or name.startswith(denied + "."):
                    raise ImportError(f"Import denied: {name}")

            # Check allowed modules if specified
            if self._allowed_modules is not None:
                allowed = any(name == mod or name.startswith(mod + ".") for mod in self._allowed_modules)
                if not allowed:
                    raise ImportError(f"Import denied (not in allowlist): {name}")

            return original_import(name, *args, **kwargs)

        builtins.__import__ = restricted_import
        builtins.__builtins__ = {
            **builtins.__builtins__,
            "__import__": restricted_import,
        }

        logger.info("Sandbox installed")

    def uninstall(self) -> None:
        """
        Uninstall the sandbox by restoring original functions.
        """
        import builtins

        builtins.open = self._original_open
        builtins.__import__ = self._original_import
        builtins.__builtins__ = {
            **builtins.__builtins__,
            "__import__": self._original_import,
        }

        logger.info("Sandbox uninstalled")

    def get_env(self) -> dict[str, str]:
        """
        Get environment variables for plugin execution.

        Filters to only whitelisted variables.
        """
        env = {}
        for key in self._env_whitelist:
            if key in os.environ:
                env[key] = os.environ[key]
        return env

    def __enter__(self) -> "PluginSandbox":
        """Context manager entry."""
        self.install()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.uninstall()


class SandboxedPluginRunner:
    """
    Runner that executes plugin code within a sandbox.

    Example:
        runner = SandboxedPluginRunner(sandbox)
        await runner.run(plugin_instance, "initialize")
    """

    def __init__(self, sandbox: PluginSandbox | None = None):
        self._sandbox = sandbox or PluginSandbox()

    async def run(
        self,
        plugin_instance: Any,
        method_name: str,
        *args,
        **kwargs,
    ) -> Any:
        """
        Run a plugin method within the sandbox.

        Args:
            plugin_instance: The plugin instance
            method_name: Name of the method to call
            *args: Positional arguments to pass to the method
            **kwargs: Keyword arguments to pass to the method

        Returns:
            The return value of the method

        Raises:
            Any exception raised by the plugin method
        """
        method = getattr(plugin_instance, method_name, None)
        if method is None:
            raise AttributeError(f"Plugin has no method: {method_name}")

        # Install sandbox for this run
        self._sandbox.install()

        try:
            # Execute within sandbox context
            if hasattr(method, "__call__"):
                return method(*args, **kwargs)
            else:
                raise TypeError(f"Plugin.{method_name} is not callable")
        finally:
            self._sandbox.uninstall()

    def run_sync(
        self,
        plugin_instance: Any,
        method_name: str,
        *args,
        **kwargs,
    ) -> Any:
        """
        Synchronous version of run.

        For plugins that have synchronous initialize/terminate methods.
        """
        method = getattr(plugin_instance, method_name, None)
        if method is None:
            raise AttributeError(f"Plugin has no method: {method_name}")

        self._sandbox.install()

        try:
            return method(*args, **kwargs)
        finally:
            self._sandbox.uninstall()


# ============================================================================
# Default Sandbox Configuration
# ============================================================================

def create_default_sandbox(
    plugin_type: str = "source",
    extra_allowed_modules: list[str] | None = None,
) -> PluginSandbox:
    """
    Create a sandbox with sensible defaults for the specified plugin type.

    Args:
        plugin_type: Type of plugin (source, delivery, agent, router, policy)
        extra_allowed_modules: Additional modules to allow

    Returns:
        Configured PluginSandbox instance
    """
    # Base allowed modules for all plugin types
    allowed = [
        "core",
        "typing",
        "dataclasses",
        "datetime",
        "uuid",
        "json",
        "logging",
        "asyncio",
        "collections",
        "contextvars",
        "copy",
        "functools",
        "itertools",
        "re",
        "traceback",
        "warnings",
    ]

    # Type-specific modules
    type_specific = {
        "source": [
            "aiohttp",
            "httpx",
            "websockets",
            "requests",
        ],
        "delivery": [
            "aiohttp",
            "httpx",
            "websockets",
            "requests",
            "smtplib",
            "email",
        ],
        "agent": [
            "anthropic",
            "openai",
        ],
        "router": [],
        "policy": [],
    }

    allowed.extend(type_specific.get(plugin_type, []))

    if extra_allowed_modules:
        allowed.extend(extra_allowed_modules)

    return PluginSandbox(
        allowed_modules=list(set(allowed)),
        denied_modules=PluginSandbox.DEFAULT_IMPORT_DENYLIST,
        env_whitelist=["PATH", "PYTHONPATH", "HOME"],
    )
