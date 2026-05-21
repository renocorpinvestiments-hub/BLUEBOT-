# core/queue/runtime.py
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid

from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Optional,
    Set,
    List,
)

from core.queue.broker import (
    BrokerFacade,
    DeliveryLease,
    Message,
)

# ============================================================
# CONSTANTS
# ============================================================

MONOTONIC = time.monotonic

Handler = Callable[
    [Dict[str, Any]],
    Awaitable[None],
]

# ============================================================
# HEALTH
# ============================================================


class RuntimeHealth(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    DRAINING = "draining"
    STOPPING = "stopping"
    STOPPED = "stopped"


# ============================================================
# INTERNAL MESSAGE
# ============================================================


@dataclass(slots=True)
class RuntimeEnvelope:
    message: Message
    lease: DeliveryLease
    received_at: float


# ============================================================
# STATS
# ============================================================


@dataclass(slots=True)
class RuntimeStats:
    processed: int = 0
    failed: int = 0
    timed_out: int = 0
    rejected: int = 0
    inflight: int = 0


# ============================================================
# QUEUE RUNTIME
# ============================================================


class QueueRuntime:
    """
    Institutional-grade execution runtime.

    RESPONSIBILITIES:
    - worker orchestration
    - handler execution
    - bounded concurrency
    - graceful draining
    - cancellation safety
    - telemetry
    - execution isolation

    NON-RESPONSIBILITIES:
    - retries
    - dedupe
    - leasing
    - replay protection
    - DLQ
    - idempotency
    - scheduling
    - persistence
    """

    def __init__(
        self,
        *,
        broker: BrokerFacade,
        handler_resolver: Callable[
            [str],
            Optional[Handler],
        ],
        topics: List[str],
        telemetry: Optional[Any] = None,
        consumer_id: Optional[str] = None,
        worker_count: int = 32,
        max_queue_size: int = 5000,
        handler_timeout: float = 30.0,
        shutdown_timeout: float = 30.0,
        dequeue_timeout: float = 1.0,
        consumer_restart_delay: float = 1.0,
    ):

        self._broker = broker

        self._handler_resolver = (
            handler_resolver
        )

        self._topics = tuple(
            sorted(set(topics))
        )

        self._telemetry = telemetry

        self._consumer_id = (
            consumer_id
            or f"runtime-{uuid.uuid4()}"
        )

        self._worker_count = max(
            1,
            worker_count,
        )

        self._handler_timeout = max(
            1.0,
            handler_timeout,
        )

        self._shutdown_timeout = max(
            1.0,
            shutdown_timeout,
        )

        self._dequeue_timeout = max(
            0.1,
            dequeue_timeout,
        )

        self._consumer_restart_delay = max(
            0.1,
            consumer_restart_delay,
        )

        # ====================================================
        # STATE
        # ====================================================

        self._health = RuntimeHealth.STOPPED

        self._running = False
        self._draining = False

        self._stats = RuntimeStats()

        # ====================================================
        # EXECUTION QUEUE
        # ====================================================

        self._queue: asyncio.Queue[
            RuntimeEnvelope
        ] = asyncio.Queue(
            maxsize=max_queue_size
        )

        # ====================================================
        # TASK REGISTRIES
        # ====================================================

        self._consumer_tasks: Set[
            asyncio.Task
        ] = set()

        self._worker_tasks: Set[
            asyncio.Task
        ] = set()

        # ====================================================
        # EXECUTION TRACKING
        # ====================================================

        self._active_executions: Dict[
            str,
            asyncio.Task,
        ] = {}

        self._execution_lock = asyncio.Lock()

        # ====================================================
        # SHUTDOWN SIGNAL
        # ====================================================

        self._shutdown_event = asyncio.Event()

    # ============================================================
    # LIFECYCLE
    # ============================================================

    async def start(self) -> None:

        if self._running:
            return

        self._health = RuntimeHealth.STARTING

        self._running = True

        # ====================================================
        # START CONSUMERS
        # ====================================================

        for topic in self._topics:

            task = asyncio.create_task(
                self._consumer_loop(topic),
                name=f"runtime-consumer:{topic}",
            )

            self._consumer_tasks.add(task)

            task.add_done_callback(
                self._consumer_tasks.discard
            )

        # ====================================================
        # START WORKERS
        # ====================================================

        for idx in range(
            self._worker_count
        ):

            task = asyncio.create_task(
                self._worker_loop(idx),
                name=f"runtime-worker:{idx}",
            )

            self._worker_tasks.add(task)

            task.add_done_callback(
                self._worker_tasks.discard
            )

        self._health = RuntimeHealth.RUNNING

        await self._emit_metric(
            "runtime.started",
            {
                "workers": self._worker_count,
                "topics": list(self._topics),
            },
        )

    async def stop(self) -> None:

        if not self._running:
            return

        self._health = RuntimeHealth.DRAINING

        self._running = False
        self._draining = True

        # ====================================================
        # STOP CONSUMERS
        # ====================================================

        for task in tuple(
            self._consumer_tasks
        ):
            task.cancel()

        await asyncio.gather(
            *self._consumer_tasks,
            return_exceptions=True,
        )

        # ====================================================
        # DRAIN QUEUE
        # ====================================================

        try:

            await asyncio.wait_for(
                self._queue.join(),
                timeout=self._shutdown_timeout,
            )

        except asyncio.TimeoutError:

            self._health = (
                RuntimeHealth.DEGRADED
            )

        # ====================================================
        # CANCEL EXECUTIONS
        # ====================================================

        async with self._execution_lock:

            executions = tuple(
                self._active_executions.values()
            )

        for task in executions:
            task.cancel()

        await asyncio.gather(
            *executions,
            return_exceptions=True,
        )

        # ====================================================
        # STOP WORKERS
        # ====================================================

        self._health = RuntimeHealth.STOPPING

        for task in tuple(
            self._worker_tasks
        ):
            task.cancel()

        await asyncio.gather(
            *self._worker_tasks,
            return_exceptions=True,
        )

        self._shutdown_event.set()

        self._draining = False
        self._health = RuntimeHealth.STOPPED

        await self._emit_metric(
            "runtime.stopped",
            await self.stats(),
        )

    # ============================================================
    # CONSUMERS
    # ============================================================

    async def _consumer_loop(
        self,
        topic: str,
    ) -> None:

        while self._running:

            try:

                async for (
                    message,
                    lease,
                ) in self._broker.consume(
                    topic,
                    consumer_id=self._consumer_id,
                ):

                    if not self._running:
                        return

                    envelope = RuntimeEnvelope(
                        message=message,
                        lease=lease,
                        received_at=MONOTONIC(),
                    )

                    try:

                        self._queue.put_nowait(
                            envelope
                        )

                    except asyncio.QueueFull:

                        self._stats.rejected += 1

                        await asyncio.shield(
                            self._broker.nack(
                                message.id,
                                lease.lease_id,
                                reason="runtime_overloaded",
                                requeue=True,
                            )
                        )

            except asyncio.CancelledError:
                raise

            except Exception as exc:

                self._health = (
                    RuntimeHealth.DEGRADED
                )

                await self._emit_error(
                    "runtime.consumer_failure",
                    exc,
                    {
                        "topic": topic,
                    },
                )

                await asyncio.sleep(
                    self._consumer_restart_delay
                )

    # ============================================================
    # WORKERS
    # ============================================================

    async def _worker_loop(
        self,
        worker_id: int,
    ) -> None:

        while (
            self._running
            or not self._queue.empty()
        ):

            try:

                envelope = (
                    await asyncio.wait_for(
                        self._queue.get(),
                        timeout=(
                            self._dequeue_timeout
                        ),
                    )
                )

            except asyncio.TimeoutError:
                continue

            except asyncio.CancelledError:
                raise

            try:

                task = asyncio.create_task(
                    self._execute(
                        envelope
                    ),
                    name=(
                        f"runtime-exec:"
                        f"{envelope.message.id}"
                    ),
                )

                async with (
                    self._execution_lock
                ):
                    self._active_executions[
                        envelope.message.id
                    ] = task

                await task

            finally:

                async with (
                    self._execution_lock
                ):
                    self._active_executions.pop(
                        envelope.message.id,
                        None,
                    )

                self._queue.task_done()

    # ============================================================
    # EXECUTION
    # ============================================================

    async def _execute(
        self,
        envelope: RuntimeEnvelope,
    ) -> None:

        message = envelope.message
        lease = envelope.lease

        self._stats.inflight += 1

        started = MONOTONIC()

        try:

            handler = (
                self._handler_resolver(
                    message.type
                )
            )

            # ===================================================
            # NO HANDLER
            # ===================================================

            if handler is None:

                await asyncio.shield(
                    self._broker.ack(
                        message.id,
                        lease.lease_id,
                    )
                )

                return

            # ===================================================
            # EXECUTION
            # ===================================================

            await asyncio.wait_for(
                handler(message.payload),
                timeout=self._handler_timeout,
            )

            # ===================================================
            # ACK
            # ===================================================

            await asyncio.shield(
                self._broker.ack(
                    message.id,
                    lease.lease_id,
                )
            )

            self._stats.processed += 1

            await self._emit_metric(
                "runtime.processed",
                {
                    "message_id": message.id,
                    "topic": message.topic,
                    "latency": (
                        MONOTONIC()
                        - started
                    ),
                },
            )

        except asyncio.TimeoutError:

            self._stats.timed_out += 1

            await asyncio.shield(
                self._broker.nack(
                    message.id,
                    lease.lease_id,
                    reason="handler_timeout",
                    requeue=True,
                )
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:

            self._stats.failed += 1

            await asyncio.shield(
                self._broker.nack(
                    message.id,
                    lease.lease_id,
                    reason=type(exc).__name__,
                    requeue=True,
                )
            )

            await self._emit_error(
                "runtime.handler_failure",
                exc,
                {
                    "message_id": message.id,
                    "topic": message.topic,
                    "type": message.type,
                },
            )

        finally:

            self._stats.inflight -= 1

    # ============================================================
    # STATS
    # ============================================================

    async def stats(
        self,
    ) -> Dict[str, Any]:

        return {
            "health": self._health.value,
            "running": self._running,
            "draining": self._draining,
            "processed": self._stats.processed,
            "failed": self._stats.failed,
            "timed_out": self._stats.timed_out,
            "rejected": self._stats.rejected,
            "inflight": self._stats.inflight,
            "queued": self._queue.qsize(),
            "workers": len(
                self._worker_tasks
            ),
            "consumers": len(
                self._consumer_tasks
            ),
            "active_executions": len(
                self._active_executions
            ),
        }

    # ============================================================
    # OBSERVABILITY
    # ============================================================

    async def _emit_metric(
        self,
        event: str,
        payload: Dict[str, Any],
    ) -> None:

        if not self._telemetry:
            return

        try:

            result = self._telemetry.emit(
                {
                    "event": event,
                    "payload": payload,
                    "ts": MONOTONIC(),
                }
            )

            if asyncio.iscoroutine(result):
                await result

        except Exception:
            pass

    async def _emit_error(
        self,
        event: str,
        exc: Exception,
        extra: Optional[
            Dict[str, Any]
        ] = None,
    ) -> None:

        if not self._telemetry:
            return

        payload = {
            "event": event,
            "error": str(exc),
            "type": type(exc).__name__,
            "ts": MONOTONIC(),
        }

        if extra:
            payload["extra"] = extra

        try:

            result = self._telemetry.emit(
                payload
            )

            if asyncio.iscoroutine(result):
                await result

        except Exception:
            pass
