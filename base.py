"""
Legacy compatibility layer.

Historically PulseRelay used a Signals + Module architecture inspired by Neuro.

The project is now migrating toward:

- EventEnvelope
- EventBus
- SourceAdapter
- SourceRegistry

This module remains as a compatibility bridge for older source modules.
"""

import asyncio
from abc import ABC, abstractmethod

from core.event_bus import EventBus
from core.source_adapter import SourceAdapter, SourceRegistry


class Signals:
    """
    Legacy compatibility wrapper.

    Existing source modules can continue using:

    ```python
    signals.put(key, value)
    ```

    while internally the system transitions toward EventBus.
    """

    def __init__(self):
        self._terminate = False
        self.event_bus = EventBus()
        self.queue = self.event_bus.queue
        self._sources = SourceRegistry()

    @property
    def terminate(self):
        return self._terminate

    @terminate.setter
    def terminate(self, value):
        self._terminate = value

    def put(self, key, value):
        """Compatibility wrapper around EventBus.put()."""
        self.event_bus.put(key, value)

    def register_source(self, name: str, source: "Module"):
        """Register a legacy source module."""
        self._sources.register(source)

    @property
    def sources(self):
        return self._sources


class Module(ABC):
    """
    Legacy source module abstraction.

    New source implementations should inherit from SourceAdapter instead.
    """

    def __init__(self, signals: Signals, enabled: bool = True):
        self.signals = signals
        self.enabled = enabled
        self.name = self.__class__.__name__

    def init_event_loop(self):
        """Start async event loop."""
        asyncio.run(self.run())

    @abstractmethod
    async def run(self):
        """Subclasses implement realtime source logic."""
        pass


__all__ = [
    "Signals",
    "Module",
    "SourceAdapter",
    "SourceRegistry",
]
