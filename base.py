"""
Base classes for data source modules.
参考 Neuro 的 Signals + Module 架构。
"""

import queue
import asyncio
from abc import ABC, abstractmethod


class Signals:
    """共享状态 + 统一 queue"""

    def __init__(self):
        self._terminate = False
        self.queue = queue.SimpleQueue()
        self._sources = {}

    @property
    def terminate(self):
        return self._terminate

    @terminate.setter
    def terminate(self, value):
        self._terminate = value

    def put(self, key, value):
        """放入队列，key 标识数据源类型"""
        self.queue.put((key, value))

    def register_source(self, name: str, source: "Module"):
        """注册数据源"""
        self._sources[name] = source

    @property
    def sources(self):
        return self._sources


class Module(ABC):
    """数据源基类，每个数据源一个线程"""

    def __init__(self, signals: Signals, enabled: bool = True):
        self.signals = signals
        self.enabled = enabled
        self.name = self.__class__.__name__

    def init_event_loop(self):
        """启动异步事件循环"""
        asyncio.run(self.run())

    @abstractmethod
    async def run(self):
        """子类实现具体的监听逻辑"""
        pass
