"""
core/runtime/supervisor.py

Institutional Runtime Supervisor

Responsibilities:
- async task supervision
- fault isolation
- circuit breaking
- overload protection
- adaptive recovery
- runtime orchestration
- health-aware execution
- broker supervision
- graceful degradation

Design Principles:
- async-first
- failure isolated
- self-healing
- deterministic
- broker compatible
- plugin-safe
- microservice-ready
- event-driven
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Awaitable,
    Callable,
    Dict,
    Optional,
    Set,
    Any,
)

logger = logging.getLogger("core.runtime.supervisor")


# ============================================================
# STATES
# ============================================================

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class TaskState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    STOPPED = "stopped"
    DEGRADED = "degraded"


# ============================================================
# CIRCUIT BREAKER
# ============================================================

@dataclass
class CircuitBreaker:
    """
    Institutional-grade circuit breaker.

    Prevents:
    - retry storms
    - cascading failures
    - broker collapse
    - API exhaustion
    """

    name: str

    failure_threshold: int = 5

    recovery_timeout: float = 30.0

    half_open_limit: int = 1

    state: CircuitState = CircuitState.CLOSED

    failures: int = 0

    last_failure_time: float = 0.0

    successful_half_open_calls: int = 0

    async def allow(self) -> bool:
        """
        Determines if execution is allowed.
        """

        now = time.time()

        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:

            if now - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.successful_half_open_calls = 0
                return True

            return False

        if self.state == CircuitState.HALF_OPEN:

            return (
                self.successful_half_open_calls
                < self.half_open_limit
            )

        return False

    async def record_success(self) -> None:

        if self.state == CircuitState.HALF_OPEN:

            self.successful_half_open_calls += 1

            if (
                self.successful_half_open_calls
                >= self.half_open_limit
            ):
                self.reset()

        else:
            self.failures = 0

    async def record_failure(self) -> None:

        self.failures += 1
        self.last_failure_time = time.time()

        if self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN

    def reset(self) -> None:

        self.state = CircuitState.CLOSED
        self.failures = 0
        self.successful_half_open_calls = 0


# ============================================================
# SUPERVISED TASK
# ============================================================

@dataclass
class SupervisedTask:
    """
    Runtime-supervised async task.
    """

    name: str

    coroutine_factory: Callable[[], Awaitable[Any]]

    restart_on_failure: bool = True

    max_restarts: int = 10

    restart_delay: float = 5.0

    state: TaskState = TaskState.STARTING

    restart_count: int = 0

    last_error: Optional[str] = None

    task: Optional[asyncio.Task] = None

    started_at: float = field(default_factory=time.time)


# ============================================================
# RUNTIME SUPERVISOR
# ============================================================

class RuntimeSupervisor:
    """
    Institutional-grade async runtime supervisor.

    Supervises:
    - workers
    - brokers
    - event consumers
    - websocket loops
    - plugin runtimes
    - background tasks

    Features:
    - self-healing
    - adaptive recovery
    - circuit breaking
    - overload protection
    - graceful degradation
    """

    def __init__(
        self,
        *,
        max_concurrent_tasks: int = 1000,
    ) -> None:

        self._tasks: Dict[str, SupervisedTask] = {}

        self._circuits: Dict[str, CircuitBreaker] = {}

        self._lock = asyncio.Lock()

        self._shutdown = False

        self._semaphore = asyncio.Semaphore(
            max_concurrent_tasks
        )

        self._background_tasks: Set[asyncio.Task] = set()

    # ========================================================
    # TASK REGISTRATION
    # ========================================================

    async def register_task(
        self,
        name: str,
        coroutine_factory: Callable[[], Awaitable[Any]],
        *,
        restart_on_failure: bool = True,
        max_restarts: int = 10,
        restart_delay: float = 5.0,
    ) -> None:
        """
        Register supervised runtime task.
        """

        async with self._lock:

            if name in self._tasks:
                logger.warning(
                    "Task already registered: %s",
                    name,
                )
                return

            task = SupervisedTask(
                name=name,
                coroutine_factory=coroutine_factory,
                restart_on_failure=restart_on_failure,
                max_restarts=max_restarts,
                restart_delay=restart_delay,
            )

            self._tasks[name] = task

            logger.info(
                "Registered supervised task: %s",
                name,
            )

    # ========================================================
    # START TASK
    # ========================================================

    async def start_task(
        self,
        name: str,
    ) -> None:

        supervised = self._tasks.get(name)

        if not supervised:
            raise RuntimeError(
                f"Unknown supervised task: {name}"
            )

        runtime_task = asyncio.create_task(
            self._task_runner(supervised)
        )

        supervised.task = runtime_task

        self._background_tasks.add(runtime_task)

        runtime_task.add_done_callback(
            self._background_tasks.discard
        )

    # ========================================================
    # INTERNAL TASK RUNNER
    # ========================================================

    async def _task_runner(
        self,
        supervised: SupervisedTask,
    ) -> None:

        while not self._shutdown:

            try:

                supervised.state = TaskState.RUNNING

                async with self._semaphore:
                    await supervised.coroutine_factory()

                supervised.state = TaskState.STOPPED

                return

            except asyncio.CancelledError:
                supervised.state = TaskState.STOPPED
                raise

            except Exception as exc:

                supervised.state = TaskState.FAILED
                supervised.last_error = str(exc)

                logger.exception(
                    "Supervised task failed: %s",
                    supervised.name,
                )

                if not supervised.restart_on_failure:
                    return

                supervised.restart_count += 1

                if (
                    supervised.restart_count
                    > supervised.max_restarts
                ):
                    logger.critical(
                        "Task exceeded max restarts: %s",
                        supervised.name,
                    )

                    supervised.state = TaskState.DEGRADED

                    return

                await asyncio.sleep(
                    supervised.restart_delay
                )

    # ========================================================
    # CIRCUIT MANAGEMENT
    # ========================================================

    async def register_circuit(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ) -> None:

        async with self._lock:

            if name in self._circuits:
                return

            self._circuits[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
            )

    async def circuit(
        self,
        name: str,
    ) -> CircuitBreaker:

        if name not in self._circuits:

            await self.register_circuit(name)

        return self._circuits[name]

    # ========================================================
    # HEALTH SNAPSHOT
    # ========================================================

    async def snapshot(self) -> Dict[str, Any]:
        """
        Runtime-safe supervision snapshot.
        """

        return {
            "shutdown": self._shutdown,
            "tasks": {
                name: {
                    "state": task.state.value,
                    "restarts": task.restart_count,
                    "last_error": task.last_error,
                    "started_at": task.started_at,
                }
                for name, task in self._tasks.items()
            },
            "circuits": {
                name: {
                    "state": circuit.state.value,
                    "failures": circuit.failures,
                }
                for name, circuit in self._circuits.items()
            },
        }

    # ========================================================
    # SHUTDOWN
    # ========================================================

    async def shutdown(self) -> None:
        """
        Graceful supervisor shutdown.
        """

        self._shutdown = True

        for task in self._background_tasks:
            task.cancel()

        if self._background_tasks:

            await asyncio.gather(
                *self._background_tasks,
                return_exceptions=True,
            )

        logger.info(
            "Runtime supervisor shutdown complete."
        )


# ============================================================
# GLOBAL INSTANCE
# ============================================================

runtime_supervisor = RuntimeSupervisor()
