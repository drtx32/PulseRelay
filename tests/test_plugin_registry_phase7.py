"""
Phase 7 plugin system tests.
"""

from __future__ import annotations

import textwrap

import pytest

from core.plugin.manifest import PluginEntryPoint, PluginState
from core.plugin.registry import PluginRegistry


@pytest.fixture
def temp_plugin_dir(tmp_path):
    plugin_file = tmp_path / "phase7_temp_plugin.py"
    plugin_file.write_text(
        textwrap.dedent(
            """
            from core.plugin.base import SourcePlugin
            from core.plugin.manifest import PluginMetadata, PluginEntryPoint

            class Phase7TempPlugin(SourcePlugin):
                metadata = PluginMetadata(
                    id="phase7-temp-source",
                    name="Phase7 Temp Source",
                    version="1.0.0",
                    entry_points=[PluginEntryPoint.SOURCE],
                )

                def __init__(self, config=None):
                    super().__init__(config=config)
                    self.initialized = False
                    self.terminated = False

                async def initialize(self):
                    self.initialized = True

                async def terminate(self):
                    self.terminated = True

                async def run(self):
                    return None
            """
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_phase7_discover_plugins_registers_entry_points(temp_plugin_dir):
    registry = PluginRegistry(plugins_dir=str(temp_plugin_dir))
    try:
        results = registry.discover_plugins()

        assert any(result.success for result in results)
        manifest = registry.get("phase7-temp-source")
        assert manifest is not None
        assert manifest.entry_point == PluginEntryPoint.SOURCE

        source_plugins = registry.list_by_entry_point(PluginEntryPoint.SOURCE)
        assert any(p.plugin_id == "phase7-temp-source" for p in source_plugins)
    finally:
        registry.shutdown()


@pytest.mark.asyncio
async def test_phase7_plugin_lifecycle_initialize_and_terminate(temp_plugin_dir):
    registry = PluginRegistry(plugins_dir=str(temp_plugin_dir))
    try:
        registry.discover_plugins()

        init_result = await registry.initialize_plugin("phase7-temp-source")
        assert init_result.success is True

        manifest = registry.get("phase7-temp-source")
        assert manifest is not None
        assert manifest.state == PluginState.ACTIVE

        instance = registry.get_instance("phase7-temp-source")
        assert instance is not None
        assert instance.initialized is True

        terminate_result = await registry.terminate_plugin("phase7-temp-source")
        assert terminate_result.success is True

        manifest = registry.get("phase7-temp-source")
        assert manifest is not None
        assert manifest.state == PluginState.INACTIVE
    finally:
        registry.shutdown()
