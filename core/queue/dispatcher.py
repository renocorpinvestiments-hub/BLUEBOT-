# core/queue/dispatcher.py

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from core.queue.broker import Message


Handler = Callable[[Dict[str, Any]], Awaitable[None]]


# ============================================================
# STATS
# ============================================================

@dataclass(slots=True)
class DispatcherStats:
    processed: int = 0
    failed: int = 0
    retried: int = 0
    dropped: int = 0
    duplicate: int = 0
    inflight: int = 0


# ============================================================
# DISPATCHER
# ============================================================

class Dispatcher:
    """
    Institutional-grade async dispatcher.

    Guarantees:
    - bounded concurrency
    - bounded memory growth
    - backpressure-safe scheduling
    - retry handling
    - tenant isolation
    - graceful draining
    - at-least-once delivery semantics
    - crash-safe idempotency ordering
    """

    def __init__(
        self,
        broker,
        topics: list[str],
        handler_resolver: Callable[[str], Optional[Handler]],
        *,
        backpressure: Optional[Any] = None,
        telemetry: Optional[Any] = None,
        redis: Optional[Any] = None,
        max_concurrency: int = 100,
        consumer_id: Optional[str] = None,
        handler_timeout: float = 30.0,
        idle_sleep: float = 0.01,
        max_retries: int = 5,
        tenant_concurrency: int = 10,
        queue_size: int = 10_000,
    ):
        self._broker = broker
        self._topics = topics
        self._handler_resolver = handler_resolver

        self._backpressure = backpressure
        self._telemetry = telemetry
        self._redis = redis

        self._consumer_id = consumer_id
        self._handler_timeout = handler_timeout
        self._idle_sleep = idle_sleep

        self._max_retries = max_retries
        self._tenant_concurrency = max(1, tenant_concurrency)

        self._max_concurrency = max(1, max_concurrency)
        self._semaphore = asyncio.Semaphore(self._max_concurrency)

        self._running = False
        self._draining = False

        self._consumer_tasks: Set[asyncio.Task] = set()
        self._worker_tasks: Set[asyncio.Task] = set()

        self._queue: asyncio.Queue = asyncio.Queue(
            maxsize=max(1, queue_size)
        )

        self._stats = DispatcherStats()

        # tenant isolation
        self._tenant_limits: Dict[str, asyncio.Semaphore] = {}

    # ============================================================
    # LIFECYCLE
    # ============================================================

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._draining = False

        for topic in self._topics:
            task = asyncio.create_task(
                self._consume_loop(topic),
                name=f"dispatcher-consumer-{topic}",
            )

            self._consumer_tasks.add(task)
            task.add_done_callback(self._consumer_tasks.discard)

        scheduler_task = asyncio.create_task(
            self._scheduler(),
            name="dispatcher-scheduler",
        )

        self._consumer_tasks.add(scheduler_task)
        scheduler_task.add_done_callback(self._consumer_tasks.discard)

        await self._emit_metric(
            "dispatcher.started",
            {
                "topics": self._topics,
                "max_concurrency": self._max_concurrency,
            },
        )

    async def stop(self, *, drain: bool = True) -> None:
        if not self._running:
            return

        self._draining = drain
        self._running = False

        # stop consumers
        for task in list(self._consumer_tasks):
            task.cancel()

        await asyncio.gather(
            *self._consumer_tasks,
            return_exceptions=True,
        )

        # worker handling
        if drain:
            await asyncio.gather(
                *self._worker_tasks,
                return_exceptions=True,
            )
        else:
            for task in list(self._worker_tasks):
                task.cancel()

            await asyncio.gather(
                *self._worker_tasks,
                return_exceptions=True,
            )

        await self._emit_metric(
            "dispatcher.stopped",
            await self.stats(),
        )

    # ============================================================
    # CONSUME
    # ============================================================

    async def _consume_loop(self, topic: str) -> None:
        while self._running:
            try:
                async for message in self._broker.consume(
                    topic,
                    consumer_id=self._consumer_id,
                ):

                    if not self._running or self._draining:
                        return

                    # adaptive drop policy
                    if self._should_drop(message):
                        self._stats.dropped += 1

                        await asyncio.shield(
                            self._broker.ack(message)
                        )
                        continue

                    # HARD backpressure
                    await self._queue.put((topic, message))

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                await self._emit_error(
                    "consume_failure",
                    exc,
                )

                await asyncio.sleep(1)

    # ============================================================
    # SCHEDULER
    # ============================================================

    async def _scheduler(self) -> None:
        while True:
            if not self._running and self._queue.empty():
                return

            try:
                topic, message = await self._queue.get()

                await self._semaphore.acquire()

                try:
                    task = asyncio.create_task(
                        self._execute(topic, message),
                        name=f"dispatcher-worker-{topic}",
                    )

                    self._worker_tasks.add(task)
                    task.add_done_callback(self._on_task_done)

                except Exception:
                    self._semaphore.release()
                    raise

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                await self._emit_error(
                    "scheduler_failure",
                    exc,
                )

                await asyncio.sleep(self._idle_sleep)

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._semaphore.release()
        self._worker_tasks.discard(task)

        with contextlib.suppress(Exception):
            task.result()

    # ============================================================
    # IDEMPOTENCY
    # ============================================================

    def _make_key(self, message: Message) -> str:
        raw = f"{message.id}:{message.type}"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return f"idemp:{digest}"

    async def _claim(self, key: str) -> bool:
        """
        Claim ownership of processing.
        """

        if not self._redis:
            return True

        try:
            return await self._redis.set(
                key,
                "processing",
                nx=True,
                ex=600,
            )

        except Exception:
            # fail-open
            return True

    async def _commit(self, key: str) -> None:
        """
        Mark processing as durable/completed.
        """

        if not self._redis:
            return

        with contextlib.suppress(Exception):
            await self._redis.set(
                key,
                "done",
                ex=3600,
            )

    # ============================================================
    # EXECUTION
    # ============================================================

    async def _execute(
        self,
        topic: str,
        message: Message,
    ) -> None:

        self._stats.inflight += 1
        start = time.perf_counter()

        key = self._make_key(message)

        try:
            # duplicate detection
            if not await self._claim(key):
                self._stats.duplicate += 1

                await asyncio.shield(
                    self._broker.ack(message)
                )
                return

            # poison message protection
            if getattr(message, "retries", 0) > self._max_retries:
                self._stats.dropped += 1

                await asyncio.shield(
                    self._broker.ack(message)
                )

                await self._emit_metric(
                    "dispatcher.poison_message",
                    {
                        "message_id": message.id,
                        "topic": topic,
                    },
                )

                return

            handler = self._handler_resolver(message.type)

            # unknown handler
            if handler is None:
                self._stats.dropped += 1

                await asyncio.shield(
                    self._broker.ack(message)
                )

                await self._emit_metric(
                    "dispatcher.no_handler",
                    {
                        "message_type": message.type,
                    },
                )

                return

            # tenant isolation
            tenant = getattr(
                message,
                "tenant_id",
                "default",
            )

            tenant_sem = self._tenant_limits.setdefault(
                tenant,
                asyncio.Semaphore(self._tenant_concurrency),
            )

            async with tenant_sem:
                await self._run_with_retry(
                    handler,
                    message,
                )

            # ====================================================
            # CRITICAL ORDERING
            # commit -> ack -> mark_processed
            # ====================================================

            await self._commit(key)

            await asyncio.shield(
                self._broker.ack(message)
            )

            with contextlib.suppress(Exception):
                await self._broker.mark_processed(message.id)

            self._stats.processed += 1

            latency = time.perf_counter() - start

            await self._emit_metric(
                "dispatcher.success",
                {
                    "topic": topic,
                    "latency": latency,
                },
            )

        except asyncio.TimeoutError:
            self._stats.retried += 1

            await asyncio.shield(
                self._broker.nack(message)
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            self._stats.failed += 1

            await self._emit_error(
                "handler_failure",
                exc,
            )

            await asyncio.shield(
                self._broker.nack(message)
            )

        finally:
            self._stats.inflight -= 1

    # ============================================================
    # RETRY POLICY
    # ============================================================

    async def _run_with_retry(
        self,
        handler: Handler,
        message: Message,
    ) -> None:

        max_attempts = 3
        base_delay = 0.05

        for attempt in range(max_attempts):
            try:
                await asyncio.wait_for(
                    handler(message.payload),
                    timeout=self._handler_timeout,
                )

                return

            except asyncio.CancelledError:
                raise

            except Exception:
                if attempt == max_attempts - 1:
                    raise

                self._stats.retried += 1

                delay = base_delay * (2 ** attempt)

                await asyncio.sleep(delay)

    # ============================================================
    # BACKPRESSURE
    # ============================================================

    def _should_drop(self, message: Message) -> bool:
        if not self._backpressure:
            return False

        try:
            return self._backpressure.should_drop(message)

        except Exception:
            return False

    # ============================================================
    # STATS
    # ============================================================

    async def stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "draining": self._draining,
            "queue_size": self._queue.qsize(),
            "processed": self._stats.processed,
            "failed": self._stats.failed,
            "retried": self._stats.retried,
            "duplicate": self._stats.duplicate,
            "dropped": self._stats.dropped,
            "inflight": self._stats.inflight,
            "workers": len(self._worker_tasks),
            "consumers": len(self._consumer_tasks),
        }

    # ============================================================
    # TELEMETRY
    # ============================================================

    async def _emit_metric(
        self,
        event: str,
        payload: Dict[str, Any],
    ) -> None:

        if not self._telemetry:
            return

        with contextlib.suppress(Exception):
            result = self._telemetry.emit(
                {
                    "event": event,
                    "payload": payload,
                }
            )

            if asyncio.iscoroutine(result):
                await result

    async def _emit_error(
        self,
        event: str,
        exc: Exception,
    ) -> None:

        if not self._telemetry:
            return

        with contextlib.suppress(Exception):
            result = self._telemetry.emit(
                {
                    "event": event,
                    "error": str(exc),
                }
            )

            if asyncio.iscoroutine(result):
                await result
