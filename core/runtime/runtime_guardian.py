# core/runtime/runtime_guardian.py

from __future__ import annotations

import asyncio
import gc
import logging
import time
from collections import defaultdict, deque
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Set, Deque, Optional, Callable, Any

logger = logging.getLogger("runtime_guardian")


# =========================================================
# RUNTIME MODES
# =========================================================

class RuntimeMode(str, Enum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    RECOVERY = "RECOVERY"
    MAINTENANCE = "MAINTENANCE"


# =========================================================
# IMMUTABLE SNAPSHOT
# =========================================================

@dataclass(frozen=True)
class RuntimeSnapshot:
    mode: RuntimeMode
    uptime: float
    total_tasks: int
    active_tasks: int
    failed_tasks: int
    loop_latency_ms: float
    pressure_score: float
    quarantined: int
    timestamp: float


# =========================================================
# CIRCUIT BREAKER
# =========================================================

@dataclass
class CircuitState:
    failures: int = 0
    opened_until: float = 0.0


# =========================================================
# MAIN GUARDIAN
# =========================================================

class RuntimeGuardian:
    """
    Institutional Runtime Governor

    Goals:
    - Zero business logic
    - Minimal coupling
    - Async-native
    - Scalable
    - Idempotent
    - Lightweight
    - Observable
    - Self-healing
    """

    _instance: Optional["RuntimeGuardian"] = None

    # =====================================================
    # SINGLETON
    # =====================================================

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # =====================================================
    # INIT
    # =====================================================

    def __init__(
        self,
        max_tasks: int = 5000,
        max_task_age: int = 300,
        monitor_interval: float = 5.0,
        circuit_cooldown: int = 30,
    ) -> None:

        if getattr(self, "_initialized", False):
            return

        self._initialized = True

        self.start_time = time.time()

        self.mode = RuntimeMode.NORMAL

        self.max_tasks = max_tasks
        self.max_task_age = max_task_age
        self.monitor_interval = monitor_interval
        self.circuit_cooldown = circuit_cooldown

        self._tasks: Dict[asyncio.Task, float] = {}
        self._task_failures = 0

        self._quarantined: Dict[str, float] = {}
        self._circuits: Dict[str, CircuitState] = defaultdict(CircuitState)

        self._latencies: Deque[float] = deque(maxlen=100)

        self._lock = asyncio.Lock()

        self._monitor_task: Optional[asyncio.Task] = None

    # =====================================================
    # START / STOP
    # =====================================================

    async def start(self) -> None:
        if self._monitor_task:
            return

        self._monitor_task = asyncio.create_task(
            self._monitor_loop(),
            name="runtime_guardian_monitor",
        )

        logger.info("RuntimeGuardian started")

    async def stop(self) -> None:
        if not self._monitor_task:
            return

        self._monitor_task.cancel()

        with suppress(asyncio.CancelledError):
            await self._monitor_task

        self._monitor_task = None

        logger.info("RuntimeGuardian stopped")

    # =====================================================
    # TASK TRACKING
    # =====================================================

    def track_task(self, task: asyncio.Task) -> asyncio.Task:
        """
        Non-invasive task tracking.
        """

        self._tasks[task] = time.time()

        task.add_done_callback(self._on_task_done)

        return task

    def create_task(
        self,
        coro,
        *,
        name: Optional[str] = None,
    ) -> asyncio.Task:
        """
        Safe centralized task creation.
        """

        task = asyncio.create_task(coro, name=name)

        return self.track_task(task)

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._tasks.pop(task, None)

        with suppress(Exception):
            if task.exception():
                self._task_failures += 1

    # =====================================================
    # CIRCUIT BREAKERS
    # =====================================================

    def allow(self, key: str) -> bool:
        """
        Fast lock-free read path.
        """

        state = self._circuits[key]

        return time.time() >= state.opened_until

    def record_failure(
        self,
        key: str,
        threshold: int = 5,
    ) -> None:

        state = self._circuits[key]

        state.failures += 1

        if state.failures >= threshold:
            state.opened_until = (
                time.time() + self.circuit_cooldown
            )

            logger.warning(
                "Circuit opened: %s",
                key,
            )

    def record_success(self, key: str) -> None:
        self._circuits.pop(key, None)

    # =====================================================
    # QUARANTINE
    # =====================================================

    def quarantine(
        self,
        name: str,
        cooldown: int = 60,
    ) -> None:

        self._quarantined[name] = (
            time.time() + cooldown
        )

        logger.warning(
            "Quarantined: %s",
            name,
        )

    def is_quarantined(self, name: str) -> bool:
        expires = self._quarantined.get(name)

        if not expires:
            return False

        if time.time() >= expires:
            self._quarantined.pop(name, None)
            return False

        return True

    # =====================================================
    # SNAPSHOT
    # =====================================================

    def snapshot(self) -> RuntimeSnapshot:

        latency = (
            sum(self._latencies) / len(self._latencies)
            if self._latencies
            else 0.0
        )

        pressure = min(
            len(self._tasks) / self.max_tasks,
            1.0,
        )

        return RuntimeSnapshot(
            mode=self.mode,
            uptime=time.time() - self.start_time,
            total_tasks=len(self._tasks),
            active_tasks=sum(
                1 for t in self._tasks if not t.done()
            ),
            failed_tasks=self._task_failures,
            loop_latency_ms=round(latency * 1000, 2),
            pressure_score=round(pressure, 2),
            quarantined=len(self._quarantined),
            timestamp=time.time(),
        )

    # =====================================================
    # MONITOR LOOP
    # =====================================================

    async def _monitor_loop(self) -> None:

        while True:

            started = time.perf_counter()

            try:
                await self._health_check()
                await self._sweep_tasks()
                self._update_mode()

            except Exception:
                logger.exception(
                    "RuntimeGuardian monitor failure"
                )

            elapsed = (
                time.perf_counter() - started
            )

            self._latencies.append(elapsed)

            await asyncio.sleep(
                self.monitor_interval
            )

    # =====================================================
    # HEALTH CHECK
    # =====================================================

    async def _health_check(self) -> None:

        # lightweight memory cleanup
        gc.collect(0)

        # cleanup expired quarantine entries
        now = time.time()

        expired = [
            k
            for k, v in self._quarantined.items()
            if now >= v
        ]

        for key in expired:
            self._quarantined.pop(key, None)

    # =====================================================
    # TASK SWEEPER
    # =====================================================

    async def _sweep_tasks(self) -> None:

        now = time.time()

        stale = []

        for task, created in self._tasks.items():

            if task.done():
                continue

            age = now - created

            if age >= self.max_task_age:
                stale.append(task)

        for task in stale:

            logger.warning(
                "Cancelling stale task: %s",
                task.get_name(),
            )

            task.cancel()

    # =====================================================
    # MODE MANAGEMENT
    # =====================================================

    def _update_mode(self) -> None:

        total = len(self._tasks)

        pressure = total / self.max_tasks

        if pressure >= 0.90:
            self.mode = RuntimeMode.CRITICAL

        elif pressure >= 0.70:
            self.mode = RuntimeMode.DEGRADED

        elif pressure >= 0.40:
            self.mode = RuntimeMode.RECOVERY

        else:
            self.mode = RuntimeMode.NORMAL

    # =====================================================
    # THROTTLE
    # =====================================================

    async def adaptive_sleep(self) -> None:
        """
        Global lightweight backpressure.
        """

        if self.mode == RuntimeMode.CRITICAL:
            await asyncio.sleep(1.0)

        elif self.mode == RuntimeMode.DEGRADED:
            await asyncio.sleep(0.25)

        elif self.mode == RuntimeMode.RECOVERY:
            await asyncio.sleep(0.05)


# =========================================================
# GLOBAL SINGLETON
# =========================================================

guardian = RuntimeGuardian()
