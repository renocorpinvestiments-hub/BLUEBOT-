# ============================================================
# core/runtime/engine.py
# ============================================================
# Institutional Runtime Engine
# ============================================================
#
# PURPOSE:
# ------------------------------------------------------------
# This is the CENTRAL ORCHESTRATION LAYER that connects:
#
# - Event System
# - Message System
# - Runtime State
# - Telemetry
# - Supervisor
# - Future Brokers / Plugins
#
# It is the "kernel runtime bootstrap" of the system.
#
# ============================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from core.contracts.events import (
    BaseEvent,
    EventFactory,
    validate_event,
)

from core.contracts.messages import (
    BaseMessage,
    MessageFactory,
    validate_message,
)

from core.runtime.state import (
    RuntimeState,
    RuntimeStatus,
    get_runtime_state,
)

from core.runtime.telemetry import (
    RuntimeTelemetry,
    get_telemetry,
)

from core.runtime.supervisor import runtime_supervisor

logger = logging.getLogger("core.runtime.engine")


# ============================================================
# CORE RUNTIME ENGINE
# ============================================================

class RuntimeEngine:
    """
    Institutional-grade runtime orchestration engine.

    Responsibilities:
    --------------------------------------------------------
    - unify event + message flow
    - coordinate state + telemetry
    - supervise execution lifecycle
    - provide safe async runtime loop
    - enable plugin/broker extension later
    """

    def __init__(self) -> None:

        # Core dependencies (lazy loaded)
        self.state: Optional[RuntimeState] = None
        self.telemetry: Optional[RuntimeTelemetry] = None

        # In-memory routing (default adapters)
        self._event_handlers: Dict[
            str,
            Callable[[BaseEvent], Any],
        ] = {}

        self._message_handlers: Dict[
            str,
            Callable[[BaseMessage], Any],
        ] = {}

        self._running = False

        self._event_queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
        self._message_queue: asyncio.Queue[BaseMessage] = asyncio.Queue()

    # ========================================================
    # INITIALIZATION
    # ========================================================

    async def initialize(self) -> None:
        """
        Bootstraps all runtime subsystems.
        """

        logger.info("Initializing Runtime Engine...")

        self.state = await get_runtime_state()
        self.telemetry = await get_telemetry()

        await self.state.set_status(RuntimeStatus.BOOTING)

        logger.info("Runtime Engine initialized.")

    # ========================================================
    # EVENT SYSTEM
    # ========================================================

    async def publish_event(self, event: BaseEvent) -> None:
        """
        Entry point for all system events.
        """

        validate_event(event)

        await self.telemetry.increment("events_published")

        await self._event_queue.put(event)

    async def subscribe_event(
        self,
        event_name: str,
        handler: Callable[[BaseEvent], Any],
    ) -> None:

        self._event_handlers[event_name] = handler

    async def _process_events(self) -> None:

        while self._running:

            event = await self._event_queue.get()

            try:
                await self.telemetry.increment("events_processed")

                handler = self._event_handlers.get(event.name)

                if handler:
                    await handler(event)

                else:
                    logger.warning(
                        "No handler for event: %s",
                        event.name,
                    )

            except Exception as e:

                await self.telemetry.increment("events_failed")

                logger.exception("Event processing failed: %s", e)

            finally:
                self._event_queue.task_done()

    # ========================================================
    # MESSAGE SYSTEM
    # ========================================================

    async def send_message(
        self,
        message: BaseMessage,
    ) -> Optional[Any]:

        validate_message(message)

        await self.telemetry.increment("messages_sent")

        await self._message_queue.put(message)

        return None

    async def register_message_handler(
        self,
        message_name: str,
        handler: Callable[[BaseMessage], Any],
    ) -> None:

        self._message_handlers[message_name] = handler

    async def _process_messages(self) -> None:

        while self._running:

            message = await self._message_queue.get()

            try:
                await self.telemetry.increment("messages_processed")

                handler = self._message_handlers.get(message.name)

                if handler:
                    await handler(message)

            except Exception as e:

                await self.telemetry.increment("message_failures")

                logger.exception("Message processing failed: %s", e)

            finally:
                self._message_queue.task_done()

    # ========================================================
    # RUNTIME LOOP
    # ========================================================

    async def _runtime_loop(self) -> None:
        """
        Main system execution loop.
        """

        await asyncio.gather(
            self._process_events(),
            self._process_messages(),
        )

    # ========================================================
    # START ENGINE
    # ========================================================

    async def start(self) -> None:
        """
        Starts full runtime system.
        """

        if self._running:
            return

        self._running = True

        await self.state.set_status(RuntimeStatus.RUNNING)

        await self.telemetry.increment("runtime_starts")

        logger.info("Runtime Engine started.")

        asyncio.create_task(self._runtime_loop())

    # ========================================================
    # STOP ENGINE
    # ========================================================

    async def stop(self) -> None:
        """
        Graceful shutdown.
        """

        if not self._running:
            return

        self._running = False

        await self.state.set_status(RuntimeStatus.SHUTTING_DOWN)

        await runtime_supervisor.shutdown()

        logger.info("Runtime Engine stopped.")

    # ========================================================
    # HEALTH CHECK
    # ========================================================

    async def health(self) -> Dict[str, Any]:

        state_snapshot = await self.state.snapshot()
        telemetry_snapshot = await self.telemetry.snapshot()
        supervisor_snapshot = await runtime_supervisor.snapshot()

        return {
            "state": state_snapshot,
            "telemetry": telemetry_snapshot,
            "supervisor": supervisor_snapshot,
            "event_queue_size": self._event_queue.qsize(),
            "message_queue_size": self._message_queue.qsize(),
            "running": self._running,
        }


# ============================================================
# GLOBAL ENGINE SINGLETON
# ============================================================

_runtime_engine: Optional[RuntimeEngine] = None


async def get_runtime_engine() -> RuntimeEngine:
    """
    Singleton runtime engine.
    """

    global _runtime_engine

    if _runtime_engine is None:
        _runtime_engine = RuntimeEngine()
        await _runtime_engine.initialize()

    return _runtime_engine
