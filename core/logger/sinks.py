"""
RENOCORP CORE LOGGER SINKS
==========================

Institutional-grade async log transport layer.

Responsibilities:
- non-blocking log transport
- sink isolation
- buffered persistence
- retry-safe delivery
- async streaming
- batching support
- fault containment
- backpressure-aware writes
- lifecycle-safe shutdown
- sink health management

DOES NOT:
- format logs
- calculate metrics
- manage telemetry
- trace spans
- analyze observability
- implement business logic

Architecture:
logger/
├── manager.py      <- runtime orchestration
├── formatter.py    <- pure transformation
└── sinks.py        <- THIS FILE

Design Goals:
- async-first
- hot-reload-safe
- idempotent startup/shutdown
- fault-isolated sinks
- retry-safe delivery
- low allocation overhead
- high throughput batching
- graceful degradation
- plugin/runtime safe
- transport-only responsibilities
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import json
import os
import socket
import sys
import time
from asyncio import StreamWriter
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    Iterable,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
)

# ============================================================
# OPTIONAL INTEGRATIONS
# ============================================================

try:
    import aiofiles  # type: ignore
except Exception:  # pragma: no cover
    aiofiles = None

# ============================================================
# CONSTANTS
# ============================================================

DEFAULT_BATCH_SIZE = 512
DEFAULT_FLUSH_INTERVAL = 1.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 0.25
DEFAULT_HEALTH_FAILURE_THRESHOLD = 5
DEFAULT_QUEUE_CAPACITY = 10_000
DEFAULT_NETWORK_TIMEOUT = 5.0
DEFAULT_ROTATION_SIZE = 50 * 1024 * 1024
DEFAULT_RING_BUFFER_SIZE = 2048

# ============================================================
# TYPES
# ============================================================


class SinkError(RuntimeError):
    """Base sink exception."""


class SinkClosedError(SinkError):
    """Raised when a sink receives writes after closure."""


class SinkBackpressureError(SinkError):
    """Raised when sink queues are overloaded."""


# ============================================================
# IMMUTABLE CONFIG
# ============================================================


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry behavior for transport failures."""

    max_retries: int = DEFAULT_MAX_RETRIES
    retry_delay: float = DEFAULT_RETRY_DELAY
    exponential_backoff: bool = True


@dataclass(frozen=True, slots=True)
class SinkHealth:
    """Immutable health snapshot."""

    healthy: bool
    failure_count: int
    last_error: Optional[str]
    last_failure_ts: Optional[float]


@dataclass(frozen=True, slots=True)
class SinkConfig:
    """Shared sink configuration."""

    batch_size: int = DEFAULT_BATCH_SIZE
    flush_interval: float = DEFAULT_FLUSH_INTERVAL
    queue_capacity: int = DEFAULT_QUEUE_CAPACITY
    network_timeout: float = DEFAULT_NETWORK_TIMEOUT
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)


# ============================================================
# SERIALIZATION HELPERS
# ============================================================


def _serialize(record: Mapping[str, Any]) -> str:
    """
    Lightweight deterministic serializer.

    Formatting and schema normalization belong to formatter.py.
    This only serializes already-normalized records.
    """

    return json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


# ============================================================
# BASE SINK
# ============================================================


class BaseSink(abc.ABC):
    """
    Institutional-grade async sink base.

    Features:
    - internal buffering
    - retry-safe writes
    - health tracking
    - background flush loop
    - fault isolation
    - lifecycle-safe shutdown
    - idempotent startup
    """

    def __init__(
        self,
        name: str,
        *,
        config: Optional[SinkConfig] = None,
    ) -> None:
        self.name = name
        self.config = config or SinkConfig()

        self._queue: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue(
            maxsize=self.config.queue_capacity
        )

        self._running = False
        self._closed = False
        self._flush_task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()

        self._failure_count = 0
        self._last_error: Optional[str] = None
        self._last_failure_ts: Optional[float] = None

        self._ring_buffer: Deque[Mapping[str, Any]] = deque(
            maxlen=DEFAULT_RING_BUFFER_SIZE
        )

    # ========================================================
    # LIFECYCLE
    # ========================================================

    async def start(self) -> None:
        """Idempotent sink startup."""

        if self._running:
            return

        async with self._lock:
            if self._running:
                return

            self._running = True
            self._closed = False
            self._flush_task = asyncio.create_task(
                self._flush_loop(),
                name=f"logger-sink-{self.name}",
            )

    async def close(self) -> None:
        """Graceful async shutdown."""

        if self._closed:
            return

        self._closed = True
        self._running = False

        if self._flush_task:
            self._flush_task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task

        await self.flush()
        await self._close()

    # ========================================================
    # PUBLIC WRITE API
    # ========================================================

    async def write_batch(self, batch: Sequence[Mapping[str, Any]]) -> None:
        """
        Non-blocking buffered enqueue.

        The manager controls orchestration.
        Sinks only transport.
        """

        if self._closed:
            raise SinkClosedError(f"Sink '{self.name}' is closed")

        for record in batch:
            try:
                self._queue.put_nowait(record)
            except asyncio.QueueFull as exc:
                raise SinkBackpressureError(
                    f"Sink '{self.name}' queue overloaded"
                ) from exc

    async def flush(self) -> None:
        """Force immediate flush."""

        records = []

        while not self._queue.empty() and len(records) < self.config.batch_size:
            with contextlib.suppress(asyncio.QueueEmpty):
                records.append(self._queue.get_nowait())

        if not records:
            return

        await self._safe_deliver(records)

    # ========================================================
    # HEALTH
    # ========================================================

    @property
    def health(self) -> SinkHealth:
        return SinkHealth(
            healthy=self._failure_count < DEFAULT_HEALTH_FAILURE_THRESHOLD,
            failure_count=self._failure_count,
            last_error=self._last_error,
            last_failure_ts=self._last_failure_ts,
        )

    # ========================================================
    # INTERNAL DELIVERY
    # ========================================================

    async def _flush_loop(self) -> None:
        """Background batch flush loop."""

        try:
            while self._running:
                await asyncio.sleep(self.config.flush_interval)
                await self.flush()

        except asyncio.CancelledError:
            raise

    async def _safe_deliver(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        """Retry-safe fault-isolated transport."""

        policy = self.config.retry_policy

        for attempt in range(policy.max_retries + 1):
            try:
                await self._deliver(records)
                self._failure_count = 0
                self._last_error = None
                return

            except Exception as exc:
                self._failure_count += 1
                self._last_error = str(exc)
                self._last_failure_ts = time.time()

                self._ring_buffer.extend(records)

                if attempt >= policy.max_retries:
                    return

                delay = policy.retry_delay

                if policy.exponential_backoff:
                    delay *= 2**attempt

                await asyncio.sleep(delay)

    # ========================================================
    # ABSTRACT API
    # ========================================================

    @abc.abstractmethod
    async def _deliver(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        """Transport implementation."""

    async def _close(self) -> None:
        """Optional cleanup hook."""


# ============================================================
# STDOUT SINK
# ============================================================


class StdoutSink(BaseSink):
    """
    Ultra-lightweight stdout transport.

    Useful for:
    - containers
    - kubernetes
    - development
    - sidecar collection
    """

    def __init__(
        self,
        *,
        name: str = "stdout",
        config: Optional[SinkConfig] = None,
    ) -> None:
        super().__init__(name=name, config=config)

    async def _deliver(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        lines = "\n".join(_serialize(record) for record in records)

        await asyncio.to_thread(self._write_stdout, lines)

    @staticmethod
    def _write_stdout(payload: str) -> None:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


# ============================================================
# FILE SINK
# ============================================================


class FileSink(BaseSink):
    """
    Buffered async file transport.

    Features:
    - append-safe writes
    - rotation support
    - directory auto-creation
    - low allocation batching
    - hot-reload-safe reopening
    """

    def __init__(
        self,
        path: str | Path,
        *,
        name: str = "file",
        config: Optional[SinkConfig] = None,
        rotation_size: int = DEFAULT_ROTATION_SIZE,
    ) -> None:
        super().__init__(name=name, config=config)

        self.path = Path(path)
        self.rotation_size = rotation_size

        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def _deliver(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        payload = "\n".join(_serialize(r) for r in records) + "\n"

        await self._rotate_if_needed()

        if aiofiles:
            async with aiofiles.open(self.path, "a", encoding="utf-8") as handle:
                await handle.write(payload)
                await handle.flush()
        else:
            await asyncio.to_thread(self._sync_write, payload)

    def _sync_write(self, payload: str) -> None:
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()

    async def _rotate_if_needed(self) -> None:
        if not self.path.exists():
            return

        size = await asyncio.to_thread(os.path.getsize, self.path)

        if size < self.rotation_size:
            return

        ts = int(time.time())
        rotated = self.path.with_suffix(f".{ts}.log")

        await asyncio.to_thread(self.path.rename, rotated)


# ============================================================
# MEMORY SINK
# ============================================================


class MemorySink(BaseSink):
    """
    In-memory ring buffer sink.

    Useful for:
    - diagnostics
    - crash recovery
    - tests
    - transient debugging

    Not intended for durable persistence.
    """

    def __init__(
        self,
        *,
        name: str = "memory",
        config: Optional[SinkConfig] = None,
        capacity: int = 10_000,
    ) -> None:
        super().__init__(name=name, config=config)

        self._records: Deque[Mapping[str, Any]] = deque(maxlen=capacity)

    @property
    def records(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._records)

    async def _deliver(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        self._records.extend(records)


# ============================================================
# TCP NETWORK SINK
# ============================================================


class TCPSink(BaseSink):
    """
    Async TCP transport.

    Features:
    - reconnect-safe delivery
    - buffered streaming
    - timeout protection
    - connection reuse
    - fault isolation

    Ideal for:
    - centralized log collectors
    - log forwarders
    - distributed runtimes
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        name: str = "tcp",
        config: Optional[SinkConfig] = None,
    ) -> None:
        super().__init__(name=name, config=config)

        self.host = host
        self.port = port

        self._writer: Optional[StreamWriter] = None

    async def _deliver(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        writer = await self._get_writer()

        payload = "\n".join(_serialize(r) for r in records) + "\n"

        writer.write(payload.encode("utf-8"))

        await asyncio.wait_for(
            writer.drain(),
            timeout=self.config.network_timeout,
        )

    async def _get_writer(self) -> StreamWriter:
        if self._writer and not self._writer.is_closing():
            return self._writer

        _, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.config.network_timeout,
        )

        self._writer = writer
        return writer

    async def _close(self) -> None:
        if not self._writer:
            return

        self._writer.close()

        with contextlib.suppress(Exception):
            await self._writer.wait_closed()


# ============================================================
# UDP SINK
# ============================================================


class UDPSink(BaseSink):
    """
    Fire-and-forget datagram transport.

    Useful for:
    - ultra-low latency pipelines
    - telemetry relays
    - lossy distributed transport
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        name: str = "udp",
        config: Optional[SinkConfig] = None,
    ) -> None:
        super().__init__(name=name, config=config)

        self.host = host
        self.port = port

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)

    async def _deliver(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        payload = "\n".join(_serialize(r) for r in records)

        await asyncio.get_running_loop().sock_sendto(
            self._socket,
            payload.encode("utf-8"),
            (self.host, self.port),
        )

    async def _close(self) -> None:
        self._socket.close()


# ============================================================
# QUEUE SINK
# ============================================================


class QueueSink(BaseSink):
    """
    Adapter sink for queue-based infrastructure.

    This sink intentionally avoids implementing queue orchestration.
    It only forwards records into an external async queue.

    Compatible with:
    - core/queue/
    - broker adapters
    - streaming runtimes
    - ingestion pipelines
    """

    def __init__(
        self,
        queue: "asyncio.Queue[Mapping[str, Any]]",
        *,
        name: str = "queue",
        config: Optional[SinkConfig] = None,
    ) -> None:
        super().__init__(name=name, config=config)

        self._external_queue = queue

    async def _deliver(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        for record in records:
            await self._external_queue.put(record)


# ============================================================
# MULTIPLEXER SINK
# ============================================================


class MultiplexSink(BaseSink):
    """
    Fan-out transport sink.

    Provides fault-isolated forwarding into multiple sinks.

    This allows transport composition without modifying the
    logger manager orchestration layer.
    """

    def __init__(
        self,
        sinks: Sequence[BaseSink],
        *,
        name: str = "multiplex",
        config: Optional[SinkConfig] = None,
    ) -> None:
        super().__init__(name=name, config=config)

        self._sinks = tuple(sinks)

    async def start(self) -> None:
        await super().start()

        for sink in self._sinks:
            await sink.start()

    async def _deliver(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        await asyncio.gather(
            *(sink.write_batch(records) for sink in self._sinks),
            return_exceptions=True,
        )

    async def flush(self) -> None:
        await asyncio.gather(
            *(sink.flush() for sink in self._sinks),
            return_exceptions=True,
        )

    async def _close(self) -> None:
        await asyncio.gather(
            *(sink.close() for sink in self._sinks),
            return_exceptions=True,
        )


# ============================================================
# SINK FACTORY
# ============================================================


class SinkFactory:
    """
    Lightweight sink factory.

    Keeps construction logic centralized while preventing
    runtime systems from tightly coupling to concrete sinks.
    """

    _builders: Dict[str, Callable[..., BaseSink]] = {}

    @classmethod
    def register(
        cls,
        sink_type: str,
        builder: Callable[..., BaseSink],
    ) -> None:
        cls._builders[sink_type] = builder

    @classmethod
    def create(cls, sink_type: str, *args: Any, **kwargs: Any) -> BaseSink:
        if sink_type not in cls._builders:
            raise SinkError(f"Unknown sink type: {sink_type}")

        return cls._builders[sink_type](*args, **kwargs)


# ============================================================
# DEFAULT REGISTRATIONS
# ============================================================


SinkFactory.register("stdout", StdoutSink)
SinkFactory.register("file", FileSink)
SinkFactory.register("memory", MemorySink)
SinkFactory.register("tcp", TCPSink)
SinkFactory.register("udp", UDPSink)
SinkFactory.register("queue", QueueSink)
SinkFactory.register("multiplex", MultiplexSink)


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "BaseSink",
    "FileSink",
    "MemorySink",
    "MultiplexSink",
    "QueueSink",
    "RetryPolicy",
    "SinkBackpressureError",
    "SinkClosedError",
    "SinkConfig",
    "SinkError",
    "SinkFactory",
    "SinkHealth",
    "StdoutSink",
    "TCPSink",
    "UDPSink",
]
