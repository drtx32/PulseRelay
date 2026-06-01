"""
Plugin loader for dynamic plugin loading and discovery.

Provides utilities for discovering plugins from various sources:
- File system (local plugins directory)
- Python packages (installed via pip)
- Remote (GitHub, URL-based loading)

Based on AstrBot's plugin loading and installation patterns.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.plugin.manifest import PluginLoadResult
from urllib.request import urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)


@dataclass
class PluginLoadSpec:
    """Specification for loading a plugin."""

    id: str
    name: str
    version: str = "0.1.0"
    source: str = "local"  # local, pypi, git, url
    location: str = ""  # Path, package name, git URL, or download URL
    checksum: str = ""  # SHA256 checksum for verification
    dependencies: list[str] = field(default_factory=list)


class PluginLoader:
    """
    Dynamic plugin loader supporting multiple loading strategies.

    Supports:
    - Local filesystem plugins
    - Python package plugins (pip install)
    - GitHub repository plugins
    - Direct URL downloads
    """

    def __init__(self, plugins_dir: str = "./plugins"):
        self._plugins_dir = Path(plugins_dir)
        self._plugins_dir.mkdir(parents=True, exist_ok=True)
        self._loaded_sources: dict[str, str] = {}  # plugin_id -> source path

    @property
    def plugins_dir(self) -> Path:
        """The plugins directory path."""
        return self._plugins_dir

    # -------------------------------------------------------------------------
    # Local Loading
    # -------------------------------------------------------------------------

    def load_local(self, plugin_path: str | Path) -> tuple[str, Any]:
        """
        Load a plugin from a local path.

        Args:
            plugin_path: Path to the plugin directory or module file

        Returns:
            Tuple of (plugin_id, module)
        """
        path = Path(plugin_path)

        if not path.exists():
            raise FileNotFoundError(f"Plugin path does not exist: {path}")

        # Determine module name
        if path.is_dir():
            module_name = path.name
            module_path = path / "__init__.py"
            if not module_path.exists():
                raise ValueError(f"Plugin directory must contain __init__.py: {path}")
        else:
            module_name = path.stem
            module_path = path

        # Load the module
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load plugin module: {module_name}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Get plugin ID from module
        plugin_id = self._extract_plugin_id(module, module_name)

        self._loaded_sources[plugin_id] = str(path)
        logger.info(f"Loaded local plugin: {plugin_id} from {path}")

        return plugin_id, module

    # -------------------------------------------------------------------------
    # Package Loading (pip)
    # -------------------------------------------------------------------------

    def install_package(self, package_name: str, version: str = "") -> bool:
        """
        Install a plugin package via pip.

        Args:
            package_name: Name of the pip package
            version: Optional version constraint

        Returns:
            True if installation succeeded
        """
        package_spec = f"{package_name}=={version}" if version else package_name

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package_spec],
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode == 0:
                logger.info(f"Installed plugin package: {package_spec}")
                return True
            else:
                logger.error(f"Failed to install {package_spec}: {result.stderr}")
                return False

        except Exception as exc:
            logger.error(f"Error installing {package_spec}: {exc}")
            return False

    def load_package(self, package_name: str) -> tuple[str, Any]:
        """
        Load a plugin from an installed Python package.

        Args:
            package_name: Name of the installed package

        Returns:
            Tuple of (plugin_id, module)
        """
        try:
            module = importlib.import_module(package_name)
            plugin_id = self._extract_plugin_id(module, package_name)
            self._loaded_sources[plugin_id] = f"package:{package_name}"
            logger.info(f"Loaded plugin package: {plugin_id}")
            return plugin_id, module

        except ImportError as exc:
            raise ImportError(f"Plugin package not installed: {package_name}") from exc

    # -------------------------------------------------------------------------
    # GitHub Loading
    # -------------------------------------------------------------------------

    def download_from_github(
        self,
        repo: str,
        branch: str = "main",
        target_dir: str | None = None,
        proxy: str = "",
    ) -> Path:
        """
        Download a plugin from a GitHub repository.

        Args:
            repo: GitHub repository (e.g., "user/repo" or full URL)
            branch: Branch to download from
            target_dir: Optional target directory within the repo
            proxy: Optional proxy URL

        Returns:
            Path to the downloaded plugin directory
        """
        # Normalize repo URL
        if not repo.startswith("http"):
            repo = f"https://github.com/{repo}"

        # Build download URL (archive)
        archive_url = f"{repo}/archive/{branch}.zip"
        if proxy:
            archive_url = f"{proxy}/{archive_url}"

        # Download
        logger.info(f"Downloading plugin from: {archive_url}")

        try:
            with urlopen(archive_url, timeout=30) as response:
                zip_data = response.read()
        except URLError as exc:
            raise RuntimeError(f"Failed to download from {archive_url}: {exc}") from exc

        # Extract to temp directory
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            zip_path = tmp_path / "plugin.zip"

            with open(zip_path, "wb") as f:
                f.write(zip_data)

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp_path)

            # Find extracted directory
            extracted_dirs = list(tmp_path.iterdir())
            if not extracted_dirs:
                raise RuntimeError("Empty archive extracted")

            # The archive typically extracts to repo-branch format
            extracted_dir = extracted_dirs[0]

            # Move to plugins directory
            plugin_name = target_dir or extracted_dir.name.replace(f"-{branch}", "")
            target_path = self._plugins_dir / plugin_name

            if target_path.exists():
                shutil.rmtree(target_path)

            shutil.copytree(extracted_dir, target_path)

            logger.info(f"Downloaded plugin to: {target_path}")
            return target_path

    def load_from_github(
        self,
        repo: str,
        branch: str = "main",
        target_dir: str | None = None,
    ) -> tuple[str, Any]:
        """
        Download and load a plugin from GitHub.

        Args:
            repo: GitHub repository
            branch: Branch to download from
            target_dir: Optional target directory within the repo

        Returns:
            Tuple of (plugin_id, module)
        """
        plugin_path = self.download_from_github(repo, branch, target_dir)
        return self.load_local(plugin_path)

    # -------------------------------------------------------------------------
    # URL Loading
    # -------------------------------------------------------------------------

    def download_from_url(
        self,
        url: str,
        checksum: str = "",
        proxy: str = "",
    ) -> Path:
        """
        Download a plugin from a direct URL.

        Args:
            url: Download URL
            checksum: Optional SHA256 checksum for verification
            proxy: Optional proxy URL

        Returns:
            Path to the downloaded plugin directory
        """
        download_url = url
        if proxy:
            download_url = f"{proxy}/{url}"

        logger.info(f"Downloading plugin from: {download_url}")

        try:
            with urlopen(download_url, timeout=60) as response:
                data = response.read()
        except URLError as exc:
            raise RuntimeError(f"Failed to download from {download_url}: {exc}") from exc

        # Verify checksum if provided
        if checksum:
            actual_checksum = hashlib.sha256(data).hexdigest()
            if actual_checksum != checksum:
                raise RuntimeError(
                    f"Checksum mismatch: expected {checksum}, got {actual_checksum}"
                )

        # Save to plugins directory
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Determine filename from URL or Content-Disposition
            filename = url.split("/")[-1].split("?")[0]
            if not filename.endswith((".zip", ".tar.gz", ".tar")):
                filename += ".zip"

            file_path = tmp_path / filename

            with open(file_path, "wb") as f:
                f.write(data)

            # Extract if archive
            if filename.endswith(".zip"):
                extract_dir = tmp_path / "plugin"
                with zipfile.ZipFile(file_path, "r") as zf:
                    zf.extractall(extract_dir)

                # Find extracted content
                extracted = list(extract_dir.iterdir())
                if len(extracted) == 1 and extracted[0].is_dir():
                    plugin_dir = extracted[0]
                else:
                    plugin_dir = extract_dir
            else:
                plugin_dir = file_path

            # Copy to plugins directory
            plugin_name = plugin_dir.name
            target_path = self._plugins_dir / plugin_name

            if target_path.exists():
                shutil.rmtree(target_path)

            if plugin_dir.is_dir():
                shutil.copytree(plugin_dir, target_path)
            else:
                shutil.copy2(plugin_dir, target_path)

            logger.info(f"Downloaded plugin to: {target_path}")
            return target_path

    def load_from_url(
        self,
        url: str,
        checksum: str = "",
    ) -> tuple[str, Any]:
        """
        Download and load a plugin from a URL.

        Args:
            url: Download URL
            checksum: Optional SHA256 checksum for verification

        Returns:
            Tuple of (plugin_id, module)
        """
        plugin_path = self.download_from_url(url, checksum)
        return self.load_local(plugin_path)

    # -------------------------------------------------------------------------
    # Plugin Installation (with dependency resolution)
    # -------------------------------------------------------------------------

    def install_plugin(
        self,
        spec: PluginLoadSpec,
        on_progress: Callable[[str], None] | None = None,
    ) -> bool:
        """
        Install a plugin from a spec, handling dependencies.

        Args:
            spec: Plugin load specification
            on_progress: Optional progress callback

        Returns:
            True if installation succeeded
        """
        progress = on_progress or (lambda x: logger.info(x))

        progress(f"Installing plugin: {spec.name}")

        # Handle different sources
        if spec.source == "local":
            progress(f"Loading local plugin from: {spec.location}")
            self.load_local(spec.location)

        elif spec.source == "pypi":
            progress(f"Installing from PyPI: {spec.location}")
            if not self.install_package(spec.location, spec.version):
                return False
            self.load_package(spec.location)

        elif spec.source == "git":
            progress(f"Installing from GitHub: {spec.location}")
            self.load_from_github(spec.location, spec.version)

        elif spec.source == "url":
            progress(f"Installing from URL: {spec.location}")
            self.load_from_url(spec.location, spec.checksum)

        else:
            logger.error(f"Unknown plugin source: {spec.source}")
            return False

        # Install dependencies
        for dep in spec.dependencies:
            progress(f"Installing dependency: {dep}")
            if not self.install_package(dep):
                logger.warning(f"Failed to install dependency: {dep}")

        progress(f"Plugin installed: {spec.name}")
        return True

    # -------------------------------------------------------------------------
    # Unified Plugin Loading Interface
    # -------------------------------------------------------------------------

    def discover_plugins(self) -> list[PluginLoadResult]:
        """
        Discover all plugins in the plugins directory.

        Returns:
            List of PluginLoadResult for each discovered plugin
        """
        from core.plugin.registry import PluginRegistry

        results: list[PluginLoadResult] = []

        if not self._plugins_dir.exists():
            logger.warning(f"Plugins directory does not exist: {self._plugins_dir}")
            return results

        # Add plugins directory to sys.path for imports
        plugins_dir_str = str(self._plugins_dir)
        if plugins_dir_str not in sys.path:
            sys.path.insert(0, plugins_dir_str)

        # Discover plugin modules
        for item in self._plugins_dir.iterdir():
            if not item.is_dir() and item.suffix != ".py":
                continue

            if item.is_dir() and not (item / "__init__.py").exists():
                continue

            module_name = item.stem if item.suffix == ".py" else item.name

            try:
                result = self.load_plugin(module_name)
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

    def load_plugin(self, plugin_id: str) -> PluginLoadResult:
        """
        Load a plugin by ID.

        Attempts to load from local sources first, then package.

        Args:
            plugin_id: Plugin identifier (module name or package name)

        Returns:
            PluginLoadResult indicating success or failure
        """
        # Try local loading first
        local_path = self._plugins_dir / plugin_id
        if local_path.exists():
            try:
                self.load_local(local_path)
                self._loaded_sources[plugin_id] = str(local_path)
                return PluginLoadResult(
                    success=True,
                    error=f"Loaded from {local_path}",
                )
            except Exception as exc:
                logger.debug(f"Failed local load for {plugin_id}: {exc}")

        # Try package loading
        try:
            self.load_package(plugin_id)
            self._loaded_sources[plugin_id] = f"package:{plugin_id}"
            return PluginLoadResult(success=True)
        except ImportError as exc:
            return PluginLoadResult(
                success=False,
                error=f"Plugin not found: {plugin_id}",
            )

    def unload_plugin(self, plugin_id: str) -> bool:
        """
        Unload a plugin by ID.

        Removes the plugin module from sys.modules and cleans up.

        Args:
            plugin_id: Plugin identifier

        Returns:
            True if unload succeeded
        """
        # Remove from loaded sources
        if plugin_id not in self._loaded_sources:
            logger.warning(f"Plugin not in loaded sources: {plugin_id}")
            return False

        source = self._loaded_sources.pop(plugin_id)

        # Remove from sys.modules
        module_name = plugin_id
        if module_name in sys.modules:
            del sys.modules[module_name]
            logger.info(f"Unloaded plugin module: {module_name}")

        # If it was a local plugin, optionally remove the directory
        # (Commented out to preserve plugin files on unload)
        # if source.startswith("/") or source.startswith("./"):
        #     path = Path(source)
        #     if path.exists() and path.is_dir():
        #         shutil.rmtree(path)

        logger.info(f"Unloaded plugin: {plugin_id}")
        return True

    def reload_plugin(self, plugin_id: str) -> PluginLoadResult:
        """
        Reload a plugin by ID.

        Unloads and re-loads the plugin, preserving source location.

        Args:
            plugin_id: Plugin identifier

        Returns:
            PluginLoadResult indicating success or failure
        """
        # Get source location before unloading
        source = self._loaded_sources.get(plugin_id)
        if source is None:
            return PluginLoadResult(
                success=False,
                error=f"Plugin not loaded: {plugin_id}",
            )

        logger.info(f"Reloading plugin: {plugin_id}")

        # Unload first
        if not self.unload_plugin(plugin_id):
            return PluginLoadResult(
                success=False,
                error="Failed to unload plugin",
            )

        # Re-load based on source type
        try:
            if source.startswith("package:"):
                package_name = source.replace("package:", "")
                self.load_package(package_name)
            else:
                self.load_local(source)

            return PluginLoadResult(success=True)
        except Exception as exc:
            logger.error(f"Failed to reload plugin {plugin_id}: {exc}")
            return PluginLoadResult(
                success=False,
                error=str(exc),
            )

    def uninstall_plugin(self, plugin_id: str) -> bool:
        """
        Uninstall a plugin.

        Args:
            plugin_id: Plugin identifier

        Returns:
            True if uninstallation succeeded
        """
        source = self._loaded_sources.get(plugin_id)
        if source is None:
            logger.warning(f"Plugin not found in loaded sources: {plugin_id}")
            return False

        # Remove from loaded sources
        del self._loaded_sources[plugin_id]

        # If it's a local plugin, remove the directory
        if source.startswith("/") or source.startswith("./"):
            path = Path(source)
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                logger.info(f"Uninstalled plugin: {plugin_id}")
                return True

        # If it's a package, try to uninstall via pip
        elif source.startswith("package:"):
            package_name = source.replace("package:", "")
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "uninstall", "-y", package_name],
                    capture_output=True,
                    timeout=60,
                )
                logger.info(f"Uninstalled package: {package_name}")
                return True
            except Exception as exc:
                logger.error(f"Failed to uninstall package {package_name}: {exc}")
                return False

        return False

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _extract_plugin_id(self, module: Any, fallback_name: str) -> str:
        """Extract plugin ID from a module."""
        # Try to find a Plugin subclass
        from core.plugin.base import Plugin

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, Plugin)
                and attr is not Plugin
                and hasattr(attr, "metadata")
            ):
                return attr.metadata.id

        # Fallback to module name
        return fallback_name

    def list_installed(self) -> list[str]:
        """List plugin IDs that have been loaded."""
        return list(self._loaded_sources.keys())

    def get_plugin_source(self, plugin_id: str) -> str | None:
        """Get the source location of a loaded plugin."""
        return self._loaded_sources.get(plugin_id)
