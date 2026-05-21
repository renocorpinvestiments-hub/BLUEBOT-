# ============================================================
# core/events/bus.py
# ============================================================
# Institutional-Grade Async Event Bus
# ============================================================
#
# DESIGN GOALS
# ------------------------------------------------------------
# - Async-first
# - High-performance
# - Event-driven architecture
# - Modular monolith compatible
# - Future microservice extraction ready
# - Broker-ready (Redis/Kafka/NATS/RabbitMQ)
# - Plugin-ready
# - Idempotent-safe
# - Production-safe
# - Fully typed
# - Low coupling
# - High cohesion
# - Fault isolated
# - Non-blocking
# - Backpressure-aware
# - Retry-ready
# - Observable
#
# ============================================================
# FUTURE UPGRADE PATHS
# ============================================================
#
# CURRENT:
# In-process async event dispatch
#
# FUTURE:
# Replace transport layer only:
#
# Internal -> Redis Streams
# Internal -> Kafka
# Internal -> NATS
# Internal -> RabbitMQ
#
# NO module refactors required.
#
# ============================================================

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Coroutine,
    DefaultDict,
    Dict,
    List,
    Optional,
    Set,
)
from core.events.models import Event
# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger("core.events.bus")

# ============================================================
# TYPE DEFINITIONS
# ============================================================

EventHandler = Callable[["Event"], Awaitable[None]]

# ============================================================
# EVENT MODEL
# ============================================================




# ============================================================
# EVENT BUS
# ============================================================


class EventBus:
    """
    Institutional-grade async event bus.

    Responsibilities:
    --------------------------------------------------------
    - Publish events
    - Subscribe handlers
    - Fan-out dispatch
    - Error isolation
    - Async task scheduling
    - Background execution
    - Wildcard subscriptions
    - Idempotency protection
    - Graceful shutdown
    - Future broker abstraction

    DOES NOT:
    --------------------------------------------------------
    - Contain business logic
    - Know modules internally
    - Handle persistence
    - Depend on FastAPI

    DESIGN:
    --------------------------------------------------------
    Extremely lightweight.
    Extremely fast.
    Minimal locking.
    Async-native.
    """

    def __init__(
        self,
        *,
        max_concurrency: int = 1000,
        enable_wildcards: bool = True,
        enable_idempotency: bool = True,
        max_idempotency_cache: int = 100_000,
    ) -> None:

        self._handlers: DefaultDict[str, List[EventHandler]] = defaultdict(list)

        self._wildcard_handlers: List[tuple[str, EventHandler]] = []

        self._semaphore = asyncio.Semaphore(max_concurrency)

        self._enable_wildcards = enable_wildcards
        self._enable_idempotency = enable_idempotency

        self._processed_events: Set[str] = set()
        self._max_idempotency_cache = max_idempotency_cache

        self._running_tasks: Set[asyncio.Task] = set()

        self._shutdown = False

    # ========================================================
    # SUBSCRIPTIONS
    # ========================================================

    def subscribe(
        self,
        event_name: str,
        handler: EventHandler,
    ) -> None:
        """
        Subscribe async handler to event.

        Supports:
        ----------------------------------------------------
        reward.completed
        wallet.*
        *
        """

        if not inspect.iscoroutinefunction(handler):
            raise TypeError(
                f"Handler '{handler.__name__}' must be async."
            )

        # Wildcard registration
        if "*" in event_name:
            if not self._enable_wildcards:
                raise RuntimeError(
                    "Wildcard subscriptions disabled."
                )

            self._wildcard_handlers.append((event_name, handler))

            logger.info(
                "Wildcard handler registered: %s -> %s",
                event_name,
                handler.__name__,
            )

            return

        self._handlers[event_name].append(handler)

        logger.info(
            "Handler registered: %s -> %s",
            event_name,
            handler.__name__,
        )

    # ========================================================
    # UNSUBSCRIBE
    # ========================================================

    def unsubscribe(
        self,
        event_name: str,
        handler: EventHandler,
    ) -> None:

        handlers = self._handlers.get(event_name)

        if not handlers:
            return

        try:
            handlers.remove(handler)

            logger.info(
                "Handler removed: %s -> %s",
                event_name,
                handler.__name__,
            )

        except ValueError:
            return

    # ========================================================
    # PUBLISH
    # ========================================================

    async def publish(self, event: Event) -> None:
        """
        Publish event asynchronously.

        Safe:
        ----------------------------------------------------
        - isolated handler failures
        - non-blocking
        - concurrent fanout
        - idempotent-aware
        """

        if self._shutdown:
            raise RuntimeError("Event bus shutting down.")

        # ====================================================
        # IDEMPOTENCY PROTECTION
        # ====================================================

        if self._enable_idempotency:

            if event.event_id in self._processed_events:
                logger.warning(
                    "Duplicate event ignored: %s",
                    event.event_id,
                )
                return

            self._processed_events.add(event.event_id)

            # Prevent unbounded growth
            if (
                len(self._processed_events)
                > self._max_idempotency_cache
            ):
                self._processed_events.clear()

        # ====================================================
        # COLLECT HANDLERS
        # ====================================================

        handlers: List[EventHandler] = []

        handlers.extend(self._handlers.get(event.name, []))

        if self._enable_wildcards:

            for pattern, handler in self._wildcard_handlers:

                if self._match_wildcard(pattern, event.name):
                    handlers.append(handler)

        # Fast exit
        if not handlers:
            return

        logger.debug(
            "Dispatching event '%s' to %s handlers",
            event.name,
            len(handlers),
        )

        # ====================================================
        # CONCURRENT DISPATCH
        # ====================================================

        await asyncio.gather(
            *[
                self._execute_handler(handler, event)
                for handler in handlers
            ],
            return_exceptions=True,
        )

    # ========================================================
    # BACKGROUND PUBLISH
    # ========================================================

    def publish_nowait(self, event: Event) -> None:
        """
        Fire-and-forget publishing.

        Useful for:
        ----------------------------------------------------
        - analytics
        - notifications
        - audit logging
        - metrics
        """

        task = asyncio.create_task(self.publish(event))

        self._running_tasks.add(task)

        task.add_done_callback(self._running_tasks.discard)

    # ========================================================
    # HANDLER EXECUTION
    # ========================================================

    async def _execute_handler(
        self,
        handler: EventHandler,
        event: Event,
    ) -> None:

        async with self._semaphore:

            start = time.perf_counter()

            try:
                await handler(event)

                elapsed = (
                    time.perf_counter() - start
                ) * 1000

                logger.debug(
                    "Handler completed: %s (%0.2fms)",
                    handler.__name__,
                    elapsed,
                )

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                logger.exception(
                    "Event handler failed | "
                    "event=%s handler=%s error=%s",
                    event.name,
                    handler.__name__,
                    exc,
                )

    # ========================================================
    # WILDCARD MATCHING
    # ========================================================

    @staticmethod
    def _match_wildcard(
        pattern: str,
        event_name: str,
    ) -> bool:
        """
        Examples:
        ----------------------------------------------------
        wallet.* -> wallet.credited
        reward.* -> reward.completed
        * -> everything
        """

        if pattern == "*":
            return True

        prefix = pattern.rstrip("*")

        return event_name.startswith(prefix)

    # ========================================================
    # SHUTDOWN
    # ========================================================

    async def shutdown(self) -> None:
        """
        Graceful shutdown.

        Waits for running tasks to complete safely.
        """

        self._shutdown = True

        if not self._running_tasks:
            return

        logger.info(
            "Waiting for %s event tasks to finish...",
            len(self._running_tasks),
        )

        await asyncio.gather(
            *self._running_tasks,
            return_exceptions=True,
        )

        logger.info("Event bus shutdown complete.")

    # ========================================================
    # INTROSPECTION
    # ========================================================

    @property
    def handler_count(self) -> int:
        return sum(
            len(v)
            for v in self._handlers.values()
        )

    @property
    def registered_events(self) -> List[str]:
        return list(self._handlers.keys())


# ============================================================
# GLOBAL BUS INSTANCE
# ============================================================

event_bus = EventBus()

# ============================================================
# EXAMPLE USAGE
# ============================================================
#
# SUBSCRIBE:
#
# async def wallet_listener(event: Event):
#     ...
#
# event_bus.subscribe(
#     "reward.completed",
#     wallet_listener,
# )
#
# ------------------------------------------------------------
#
# PUBLISH:
#
# await event_bus.publish(
#     Event(
#         name="reward.completed",
#         payload={
#             "user_id": 1,
#             "amount": 50,
#         },
#     )
# )
#
# ============================================================
