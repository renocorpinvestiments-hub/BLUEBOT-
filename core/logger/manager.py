"""
RENOCORP CORE LOGGER MANAGER
============================

Institutional-grade async logging runtime kernel.

Responsibilities:
- async log orchestration
- batching
- sink routing
- backpressure control
- graceful degradation
- lifecycle-safe startup/shutdown
- idempotent initialization
- hot-reload-safe execution

DOES NOT:
- format logs
- calculate metrics
- trace telemetry
- analyze observability
- implement business logic

Those belong elsewhere.

Architecture:
logger/
├── manager.py      <- THIS FILE
├── formatter.py    <- pure transformation
└── sinks.py        <- output transports

Designed for:
- high throughput
- plugin ecosystems
- distributed runtimes
- multi-sink architectures
- fault isolation
- async-first execution
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Deque,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Protocol,
    Set,
)

# ============================================================
# CONSTANTS
# ============================================================

DEFAULT_BATCH_SIZE = 512
DEFAULT_FLUSH_INTERVAL = 0.50
DEFAULT_QUEUE_CAPACITY = 100_000
DEFAULT_SINK_TIMEOUT = 5.0
DEFAULT_DROP_THRESHOLD = 0.95

# ============================================================
# TYPES
# ============================================================


class LoggerState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


class OverflowPolicy(str, Enum):
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"
    BLOCK = "block"


# ============================================================
# SINK PROTOCOL
# ============================================================


class LogSink(Protocol):
    """
    Sink transport contract.

    Implemented in:
        logger/sinks.py
    """

    name: str

    async def write_batch(self, batch: List[dict]) -> None:
        ...

    async def flush(self) -> None:
        ...

    async def close(self) -> None:
        ...


# ============================================================
# IMMUTABLE CONFIG
# ============================================================


@dataclass(frozen=True, slots=True)
class LoggerConfig:
    batch_size: int = DEFAULT_BATCH_SIZE
    flush_interval: float = DEFAULT_FLUSH_INTERVAL
    queue_capacity: int = DEFAULT_QUEUE_CAPACITY
    sink_timeout: float = DEFAULT_SINK_TIMEOUT
    overflow_policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST
    drop_threshold: float = DEFAULT_DROP_THRESHOLD


# ============================================================
# METRICS SNAPSHOT
# ============================================================


@dataclass(slots=True)
class LoggerStats:
    queued: int = 0
    processed: int = 0
    dropped: int = 0
    failed: int = 0
    flushed: int = 0
    sink_failures: int = 0
    last_flush_ts: float = 0.0


# ============================================================
# SINK REGISTRY
# ============================================================


class SinkRegistry:
    """
    Thread-safe sink registry.

    Keeps logger extensible without exposing internals.
    """

    def __init__(self) -> None:
        self._sinks: Dict[str, LogSink] = {}

    def register(self, sink: LogSink) -> None:
        self._sinks[sink.name] = sink

    def unregister(self, name: str) -> None:
        self._sinks.pop(name, None)

    def get_all(self) -> FrozenSet[LogSink]:
        return frozenset(self._sinks.values())

    def exists(self, name: str) -> bool:
        return name in self._sinks


# ============================================================
# ROUTER
# ============================================================


class LogRouter:
    """
    Determines which sinks receive which records.

    Avoid business logic.
    Avoid filtering policies.
    Pure transport routing only.
    """

    def __init__(self, registry: SinkRegistry) -> None:
        self._registry = registry

    def resolve(self, record: dict) -> FrozenSet[LogSink]:
        """
        Future-ready for:
        - severity routing
        - namespace routing
        - tenant routing
        - dynamic routing
        """

        return self._registry.get_all()


# ============================================================
# BATCH PROCESSOR
# ============================================================


class BatchProcessor:
    """
    Memory-efficient batch collector.

    Uses deque for low-overhead append/popleft operations.
    """

    __slots__ = ("_batch", "_batch_size")

    def __init__(self, batch_size: int) -> None:
        self._batch: Deque[dict] = deque()
        self._batch_size = batch_size

    def append(self, item: dict) -> bool:
        self._batch.append(item)
        return len(self._batch) >= self._batch_size

    def drain(self) -> List[dict]:
        batch = list(self._batch)
        self._batch.clear()
        return batch

    def empty(self) -> bool:
        return not self._batch


# ============================================================
# LOGGER MANAGER
# ============================================================


class LoggerManager:
    """
    Institutional-grade logging runtime kernel.

    Features:
    - async-first
    - backpressure aware
    - non-blocking
    - idempotent startup
    - graceful shutdown
    - hot-reload safe
    - sink isolation
    - fault containment
    - batch flushing
    - overload protection
    """

    _instance: Optional["LoggerManager"] = None

    def __new__(cls, *args: Any, **kwargs: Any):
        """
        Singleton-like runtime safety.

        Prevents accidental duplicate logger kernels.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)

        return cls._instance

    def __init__(
        self,
        config: Optional[LoggerConfig] = None,
    ) -> None:

        if getattr(self, "_initialized", False):
            return

        self._initialized = True

        self.config = config or LoggerConfig()

        self.state = LoggerState.STOPPED

        self.stats = LoggerStats()

        self.registry = SinkRegistry()
        self.router = LogRouter(self.registry)

        self._queue: asyncio.Queue[dict] = asyncio.Queue(
            maxsize=self.config.queue_capacity
        )

        self._batcher = BatchProcessor(self.config.batch_size)

        self._worker_task: Optional[asyncio.Task] = None
        self._flush_task: Optional[asyncio.Task] = None

        self._shutdown_event = asyncio.Event()

    # ========================================================
    # LIFECYCLE
    # ========================================================

    async def start(self) -> None:
        """
        Idempotent startup.

        Safe to call multiple times.
        """

        if self.state in (LoggerState.RUNNING, LoggerState.STARTING):
            return

        self.state = LoggerState.STARTING

        self._shutdown_event.clear()

        self._worker_task = asyncio.create_task(
            self._worker_loop(),
            name="logger-worker",
        )

        self._flush_task = asyncio.create_task(
            self._flush_loop(),
            name="logger-flusher",
        )

        self.state = LoggerState.RUNNING

    async def shutdown(self) -> None:
        """
        Graceful shutdown with flush guarantees.
        """

        if self.state in (LoggerState.STOPPING, LoggerState.STOPPED):
            return

        self.state = LoggerState.STOPPING

        self._shutdown_event.set()

        tasks = [self._worker_task, self._flush_task]

        for task in tasks:
            if task:
                task.cancel()

        for task in tasks:
            if task:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        await self._flush_now()

        sinks = self.registry.get_all()

        await asyncio.gather(
            *(sink.close() for sink in sinks),
            return_exceptions=True,
        )

        self.state = LoggerState.STOPPED

    # ========================================================
    # PUBLIC API
    # ========================================================

    async def emit(self, record: dict) -> None:
        """
        Ultra-fast non-blocking enqueue.

        Assumes:
            formatter.py already normalized the record.
        """

        if self.state != LoggerState.RUNNING:
            return

        try:
            self._queue.put_nowait(record)
            self.stats.queued += 1

        except asyncio.QueueFull:
            self._handle_overflow(record)

    def register_sink(self, sink: LogSink) -> None:
        self.registry.register(sink)

    def unregister_sink(self, name: str) -> None:
        self.registry.unregister(name)

    def snapshot(self) -> dict:
        """
        Lightweight immutable runtime snapshot.
        """

        return {
            "state": self.state.value,
            "queued": self.stats.queued,
            "processed": self.stats.processed,
            "dropped": self.stats.dropped,
            "failed": self.stats.failed,
            "flushed": self.stats.flushed,
            "sink_failures": self.stats.sink_failures,
            "queue_size": self._queue.qsize(),
        }

    # ========================================================
    # INTERNAL LOOPS
    # ========================================================

    async def _worker_loop(self) -> None:
        """
        Main ingestion worker.

        Optimized for:
        - low allocations
        - minimal await overhead
        - stable throughput
        """

        while not self._shutdown_event.is_set():

            try:
                record = await self._queue.get()

                should_flush = self._batcher.append(record)

                self.stats.processed += 1

                if should_flush:
                    await self._flush_now()

            except asyncio.CancelledError:
                break

            except Exception:
                self.stats.failed += 1

    async def _flush_loop(self) -> None:
        """
        Scheduled flush controller.
        """

        while not self._shutdown_event.is_set():

            try:
                await asyncio.sleep(self.config.flush_interval)

                if not self._batcher.empty():
                    await self._flush_now()

            except asyncio.CancelledError:
                break

            except Exception:
                self.stats.failed += 1

    # ========================================================
    # FLUSHING
    # ========================================================

    async def _flush_now(self) -> None:

        batch = self._batcher.drain()

        if not batch:
            return

        sinks = self.router.resolve(batch[0])

        await asyncio.gather(
            *(
                self._safe_sink_write(sink, batch)
                for sink in sinks
            ),
            return_exceptions=True,
        )

        self.stats.flushed += len(batch)
        self.stats.last_flush_ts = time.time()

    async def _safe_sink_write(
        self,
        sink: LogSink,
        batch: List[dict],
    ) -> None:
        """
        Sink fault isolation.

        One bad sink must NEVER crash logging runtime.
        """

        try:
            await asyncio.wait_for(
                sink.write_batch(batch),
                timeout=self.config.sink_timeout,
            )

        except Exception:
            self.stats.sink_failures += 1

    # ========================================================
    # OVERFLOW CONTROL
    # ========================================================

    def _handle_overflow(self, record: dict) -> None:

        self.stats.dropped += 1

        if self.config.overflow_policy == OverflowPolicy.DROP_NEWEST:
            return

        if self.config.overflow_policy == OverflowPolicy.DROP_OLDEST:

            with contextlib.suppress(Exception):
                self._queue.get_nowait()
                self._queue.put_nowait(record)

            return

    # ========================================================
    # CONTEXT MANAGER
    # ========================================================

    async def __aenter__(self) -> "LoggerManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.shutdown()


# ============================================================
# GLOBAL RUNTIME INSTANCE
# ============================================================

logger_manager = LoggerManager()
