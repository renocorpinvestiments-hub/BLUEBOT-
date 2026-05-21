# core/runtime/orchestrator.py

from __future__ import annotations

import asyncio
import contextlib
import gc
import os
import time
import weakref
import statistics

from collections import deque, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Deque,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
)


# ============================================================
# RUNTIME HEALTH
# ============================================================


class RuntimeHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    RECOVERY = "RECOVERY"


class PressureLevel(str, Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ============================================================
# RUNTIME CONFIG
# ============================================================


@dataclass(slots=True)
class OrchestratorConfig:
    tick_interval: float = 0.50

    max_retry_rate: int = 2500
    max_restart_rate: int = 50
    max_event_fanout: int = 5000
    max_concurrent_tasks: int = 50000
    max_queue_depth: int = 200000

    max_deadletter_growth: int = 5000
    max_cancel_rate: int = 5000

    max_loop_latency: float = 0.150
    max_consumer_latency: float = 2.0

    metrics_window_size: int = 2048

    enable_gc_pressure_control: bool = True
    enable_telemetry_sampling: bool = True
    enable_wave_protection: bool = True
    enable_snapshotting: bool = False

    shutdown_timeout: float = 30.0

    retry_freeze_threshold: float = 0.92
    dispatch_throttle_threshold: float = 0.80

    event_depth_limit: int = 16
    event_recursion_limit: int = 128

    idle_gc_interval: float = 30.0


# ============================================================
# EXECUTION BUDGET
# ============================================================


@dataclass(slots=True)
class ExecutionBudget:
    retries: int = 0
    restarts: int = 0
    fanout: int = 0
    dispatches: int = 0
    cancellations: int = 0

    retry_budget: int = 2500
    restart_budget: int = 50
    fanout_budget: int = 5000
    dispatch_budget: int = 500000
    cancellation_budget: int = 5000

    def reset(self) -> None:
        self.retries = 0
        self.restarts = 0
        self.fanout = 0
        self.dispatches = 0
        self.cancellations = 0


# ============================================================
# HEALTH MATRIX
# ============================================================


@dataclass(slots=True)
class RuntimeHealthState:
    health: RuntimeHealth = RuntimeHealth.HEALTHY
    pressure: PressureLevel = PressureLevel.NORMAL

    queue_depth: int = 0
    retry_rate: int = 0
    restart_rate: int = 0
    deadletter_growth: int = 0
    cancellation_rate: int = 0

    loop_latency: float = 0.0
    consumer_latency: float = 0.0

    throughput: int = 0
    active_tasks: int = 0

    event_fanout: int = 0

    updated_at: float = field(default_factory=time.monotonic)


# ============================================================
# METRIC RING BUFFER
# ============================================================


class RingMetric:
    __slots__ = ("_values",)

    def __init__(self, maxlen: int = 2048) -> None:
        self._values: Deque[float] = deque(maxlen=maxlen)

    def add(self, value: float) -> None:
        self._values.append(value)

    @property
    def avg(self) -> float:
        if not self._values:
            return 0.0
        return statistics.fmean(self._values)

    @property
    def latest(self) -> float:
        if not self._values:
            return 0.0
        return self._values[-1]

    @property
    def max(self) -> float:
        if not self._values:
            return 0.0
        return max(self._values)


# ============================================================
# WAVE BREAKER
# ============================================================


class RuntimeWaveBreaker:
    """
    Detects cascading runtime instability.
    """

    __slots__ = (
        "retry_storm",
        "restart_storm",
        "fanout_storm",
        "deadletter_storm",
    )

    def __init__(self) -> None:
        self.retry_storm: bool = False
        self.restart_storm: bool = False
        self.fanout_storm: bool = False
        self.deadletter_storm: bool = False

    @property
    def active(self) -> bool:
        return (
            self.retry_storm
            or self.restart_storm
            or self.fanout_storm
            or self.deadletter_storm
        )


# ============================================================
# ADAPTIVE SCHEDULER
# ============================================================


class AdaptiveScheduler:
    """
    Adaptive pacing controller.
    """

    __slots__ = (
        "_dispatch_delay",
        "_retry_delay",
        "_consumer_delay",
    )

    def __init__(self) -> None:
        self._dispatch_delay = 0.0
        self._retry_delay = 0.0
        self._consumer_delay = 0.0

    def update(self, pressure: PressureLevel) -> None:
        if pressure == PressureLevel.NORMAL:
            self._dispatch_delay = 0.0
            self._retry_delay = 0.0
            self._consumer_delay = 0.0

        elif pressure == PressureLevel.ELEVATED:
            self._dispatch_delay = 0.001
            self._retry_delay = 0.005
            self._consumer_delay = 0.001

        elif pressure == PressureLevel.HIGH:
            self._dispatch_delay = 0.005
            self._retry_delay = 0.025
            self._consumer_delay = 0.005

        else:
            self._dispatch_delay = 0.015
            self._retry_delay = 0.100
            self._consumer_delay = 0.015

    @property
    def dispatch_delay(self) -> float:
        return self._dispatch_delay

    @property
    def retry_delay(self) -> float:
        return self._retry_delay

    @property
    def consumer_delay(self) -> float:
        return self._consumer_delay


# ============================================================
# RUNTIME SNAPSHOT ENGINE
# ============================================================


class RuntimeSnapshotEngine:
    """
    Lightweight snapshot engine.
    """

    async def snapshot(self, orchestrator: "RuntimeOrchestrator") -> Dict[str, Any]:
        return {
            "health": orchestrator.health_state.health.value,
            "pressure": orchestrator.health_state.pressure.value,
            "active_tasks": len(orchestrator._live_tasks),
            "queue_depth": orchestrator.health_state.queue_depth,
            "timestamp": time.time(),
        }


# ============================================================
# MAIN ORCHESTRATOR
# ============================================================


class RuntimeOrchestrator:
    """
    Institutional-grade runtime coordination layer.

    Responsibilities:
    - Runtime governance
    - Pressure coordination
    - Wave protection
    - Deterministic shutdown
    - Task lineage tracking
    - Adaptive telemetry
    - Retry/restart budgeting
    - Runtime stabilization
    """

    __slots__ = (
        "config",
        "health_state",
        "budget",
        "scheduler",
        "wave_breaker",
        "snapshot_engine",
        "_running",
        "_shutdown",
        "_tasks",
        "_live_tasks",
        "_task_metadata",
        "_queue_metrics",
        "_loop_latency",
        "_consumer_latency",
        "_throughput",
        "_last_gc",
        "_main_loop_task",
        "_lock",
        "_event_depth",
        "_event_recursion_guard",
        "_service_restart_tracker",
        "_telemetry_sampling_enabled",
        "_retry_frozen",
        "_dispatch_throttled",
    )

    def __init__(
        self,
        config: Optional[OrchestratorConfig] = None,
    ) -> None:

        self.config = config or OrchestratorConfig()

        self.health_state = RuntimeHealthState()

        self.budget = ExecutionBudget(
            retry_budget=self.config.max_retry_rate,
            restart_budget=self.config.max_restart_rate,
            fanout_budget=self.config.max_event_fanout,
            cancellation_budget=self.config.max_cancel_rate,
        )

        self.scheduler = AdaptiveScheduler()
        self.wave_breaker = RuntimeWaveBreaker()
        self.snapshot_engine = RuntimeSnapshotEngine()

        self._running = False
        self._shutdown = False

        self._tasks: Set[asyncio.Task[Any]] = set()

        self._live_tasks: Dict[str, asyncio.Task[Any]] = {}
        self._task_metadata: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

        self._queue_metrics: Dict[str, Dict[str, Any]] = {}
        self._service_restart_tracker: Dict[str, Deque[float]] = defaultdict(deque)

        self._loop_latency = RingMetric(self.config.metrics_window_size)
        self._consumer_latency = RingMetric(self.config.metrics_window_size)
        self._throughput = RingMetric(self.config.metrics_window_size)

        self._event_depth: Dict[str, int] = defaultdict(int)
        self._event_recursion_guard: Dict[str, int] = defaultdict(int)

        self._last_gc = time.monotonic()

        self._main_loop_task: Optional[asyncio.Task[Any]] = None

        self._lock = asyncio.Lock()

        self._telemetry_sampling_enabled = True
        self._retry_frozen = False
        self._dispatch_throttled = False

    # ========================================================
    # LIFECYCLE
    # ========================================================

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._shutdown = False

        self._main_loop_task = asyncio.create_task(
            self._runtime_loop(),
            name="runtime-orchestrator-loop",
        )

    async def stop(self) -> None:
        if self._shutdown:
            return

        self._shutdown = True
        self._running = False

        # 1. Freeze retries
        self._retry_frozen = True

        # 2. Throttle dispatch
        self._dispatch_throttled = True

        # 3. Cancel background tasks
        if self._main_loop_task:
            self._main_loop_task.cancel()

            with contextlib.suppress(Exception):
                await self._main_loop_task

        # 4. Drain tasks
        await self._drain_tasks()

        # 5. Snapshot runtime
        if self.config.enable_snapshotting:
            with contextlib.suppress(Exception):
                await self.snapshot_engine.snapshot(self)

        # 6. Final GC
        gc.collect()

    # ========================================================
    # MAIN LOOP
    # ========================================================

    async def _runtime_loop(self) -> None:
        while self._running:

            started = time.perf_counter()

            try:
                self._update_health_matrix()
                self._update_pressure()
                self._update_scheduler()
                self._detect_waves()
                self._apply_runtime_protection()
                self._adaptive_telemetry_control()
                self._cleanup_dead_tasks()
                self._gc_maintenance()

            except asyncio.CancelledError:
                raise

            except Exception:
                pass

            latency = time.perf_counter() - started

            self._loop_latency.add(latency)

            await asyncio.sleep(self.config.tick_interval)

    # ========================================================
    # HEALTH SYSTEM
    # ========================================================

    def _update_health_matrix(self) -> None:
        total_queue_depth = sum(
            q.get("depth", 0)
            for q in self._queue_metrics.values()
        )

        retry_rate = self.budget.retries
        restart_rate = self.budget.restarts

        deadletter_growth = sum(
            q.get("deadletters", 0)
            for q in self._queue_metrics.values()
        )

        cancellation_rate = self.budget.cancellations

        self.health_state.queue_depth = total_queue_depth
        self.health_state.retry_rate = retry_rate
        self.health_state.restart_rate = restart_rate
        self.health_state.deadletter_growth = deadletter_growth
        self.health_state.cancellation_rate = cancellation_rate

        self.health_state.loop_latency = self._loop_latency.avg
        self.health_state.consumer_latency = self._consumer_latency.avg
        self.health_state.active_tasks = len(self._live_tasks)

        self.health_state.updated_at = time.monotonic()

    def _update_pressure(self) -> None:

        score = 0.0

        queue_ratio = (
            self.health_state.queue_depth
            / max(self.config.max_queue_depth, 1)
        )

        retry_ratio = (
            self.health_state.retry_rate
            / max(self.config.max_retry_rate, 1)
        )

        restart_ratio = (
            self.health_state.restart_rate
            / max(self.config.max_restart_rate, 1)
        )

        task_ratio = (
            self.health_state.active_tasks
            / max(self.config.max_concurrent_tasks, 1)
        )

        score += queue_ratio * 0.35
        score += retry_ratio * 0.30
        score += restart_ratio * 0.15
        score += task_ratio * 0.20

        if score < 0.40:
            pressure = PressureLevel.NORMAL
            health = RuntimeHealth.HEALTHY

        elif score < 0.70:
            pressure = PressureLevel.ELEVATED
            health = RuntimeHealth.DEGRADED

        elif score < 0.90:
            pressure = PressureLevel.HIGH
            health = RuntimeHealth.CRITICAL

        else:
            pressure = PressureLevel.CRITICAL
            health = RuntimeHealth.RECOVERY

        self.health_state.pressure = pressure
        self.health_state.health = health

    def _update_scheduler(self) -> None:
        self.scheduler.update(self.health_state.pressure)

    # ========================================================
    # WAVE DETECTION
    # ========================================================

    def _detect_waves(self) -> None:

        self.wave_breaker.retry_storm = (
            self.health_state.retry_rate
            >= self.config.max_retry_rate
        )

        self.wave_breaker.restart_storm = (
            self.health_state.restart_rate
            >= self.config.max_restart_rate
        )

        self.wave_breaker.deadletter_storm = (
            self.health_state.deadletter_growth
            >= self.config.max_deadletter_growth
        )

        self.wave_breaker.fanout_storm = (
            self.budget.fanout
            >= self.config.max_event_fanout
        )

    def _apply_runtime_protection(self) -> None:

        if not self.wave_breaker.active:
            self._retry_frozen = False
            self._dispatch_throttled = False
            return

        self._dispatch_throttled = True

        if self.wave_breaker.retry_storm:
            self._retry_frozen = True

    # ========================================================
    # TELEMETRY CONTROL
    # ========================================================

    def _adaptive_telemetry_control(self) -> None:

        if not self.config.enable_telemetry_sampling:
            self._telemetry_sampling_enabled = False
            return

        pressure = self.health_state.pressure

        self._telemetry_sampling_enabled = (
            pressure != PressureLevel.CRITICAL
        )

    # ========================================================
    # TASK TRACKING
    # ========================================================

    def register_task(
        self,
        task: asyncio.Task[Any],
        *,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:

        task_id = name or f"task-{id(task)}"

        self._live_tasks[task_id] = task
        self._tasks.add(task)

        self._task_metadata[task] = metadata or {}

        task.add_done_callback(
            lambda t, task_id=task_id: self._finalize_task(task_id, t)
        )

        return task_id

    def _finalize_task(
        self,
        task_id: str,
        task: asyncio.Task[Any],
    ) -> None:

        self._live_tasks.pop(task_id, None)
        self._tasks.discard(task)

        if task.cancelled():
            self.budget.cancellations += 1

    async def _drain_tasks(self) -> None:

        if not self._tasks:
            return

        tasks = list(self._tasks)

        for task in tasks:
            task.cancel()

        with contextlib.suppress(Exception):
            await asyncio.wait(
                tasks,
                timeout=self.config.shutdown_timeout,
            )

    def _cleanup_dead_tasks(self) -> None:
        dead = []

        for task_id, task in self._live_tasks.items():
            if task.done():
                dead.append(task_id)

        for task_id in dead:
            self._live_tasks.pop(task_id, None)

    # ========================================================
    # QUEUE REPORTING
    # ========================================================

    async def report_queue_metrics(
        self,
        queue_name: str,
        *,
        depth: int,
        inflight: int = 0,
        deadletters: int = 0,
        retries: int = 0,
        consumer_latency: float = 0.0,
    ) -> None:

        self._queue_metrics[queue_name] = {
            "depth": depth,
            "inflight": inflight,
            "deadletters": deadletters,
            "retries": retries,
            "consumer_latency": consumer_latency,
            "updated_at": time.monotonic(),
        }

        self._consumer_latency.add(consumer_latency)

        self.budget.retries += retries

    # ========================================================
    # EVENT GOVERNANCE
    # ========================================================

    def allow_event(
        self,
        event_name: str,
    ) -> bool:

        depth = self._event_depth[event_name]
        recursion = self._event_recursion_guard[event_name]

        if depth >= self.config.event_depth_limit:
            return False

        if recursion >= self.config.event_recursion_limit:
            return False

        self._event_depth[event_name] += 1
        self._event_recursion_guard[event_name] += 1

        self.budget.fanout += 1

        return True

    def finalize_event(
        self,
        event_name: str,
    ) -> None:

        self._event_depth[event_name] = max(
            0,
            self._event_depth[event_name] - 1,
        )

    # ========================================================
    # RESTART GOVERNANCE
    # ========================================================

    def allow_restart(self, service_name: str) -> bool:

        now = time.monotonic()

        tracker = self._service_restart_tracker[service_name]

        while tracker and (now - tracker[0]) > 60:
            tracker.popleft()

        if len(tracker) >= self.config.max_restart_rate:
            return False

        tracker.append(now)

        self.budget.restarts += 1

        return True

    # ========================================================
    # PRESSURE API
    # ========================================================

    def current_pressure_policy(self) -> Dict[str, Any]:

        pressure = self.health_state.pressure

        return {
            "pressure": pressure.value,
            "retry_frozen": self._retry_frozen,
            "dispatch_throttled": self._dispatch_throttled,
            "dispatch_delay": self.scheduler.dispatch_delay,
            "retry_delay": self.scheduler.retry_delay,
            "consumer_delay": self.scheduler.consumer_delay,
            "telemetry_sampling": self._telemetry_sampling_enabled,
        }

    # ========================================================
    # MEMORY SAFETY
    # ========================================================

    def _gc_maintenance(self) -> None:

        if not self.config.enable_gc_pressure_control:
            return

        now = time.monotonic()

        if (now - self._last_gc) < self.config.idle_gc_interval:
            return

        if self.health_state.pressure == PressureLevel.CRITICAL:
            gc.collect()

        self._last_gc = now

    # ========================================================
    # PUBLIC HELPERS
    # ========================================================

    @property
    def telemetry_sampling_enabled(self) -> bool:
        return self._telemetry_sampling_enabled

    @property
    def retry_frozen(self) -> bool:
        return self._retry_frozen

    @property
    def dispatch_throttled(self) -> bool:
        return self._dispatch_throttled

    @property
    def active_tasks(self) -> int:
        return len(self._live_tasks)

    @property
    def runtime_health(self) -> RuntimeHealth:
        return self.health_state.health

    @property
    def pressure_level(self) -> PressureLevel:
        return self.health_state.pressure

    # ========================================================
    # SAFE TASK EXECUTION
    # ========================================================

    async def guarded_execution(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        timeout: Optional[float] = None,
        task_name: Optional[str] = None,
    ) -> Any:

        if self._shutdown:
            raise RuntimeError("Runtime shutting down")

        task = asyncio.create_task(coro, name=task_name)

        self.register_task(task, name=task_name)

        try:

            if timeout:
                return await asyncio.wait_for(task, timeout=timeout)

            return await task

        except asyncio.TimeoutError:
            task.cancel()
            raise

        except asyncio.CancelledError:
            task.cancel()
            raise

    # ========================================================
    # THROTTLING
    # ========================================================

    async def throttle_dispatch(self) -> None:

        if self._dispatch_throttled:
            await asyncio.sleep(self.scheduler.dispatch_delay)

    async def throttle_retry(self) -> None:

        if self._retry_frozen:
            await asyncio.sleep(self.scheduler.retry_delay)

    async def throttle_consumer(self) -> None:

        delay = self.scheduler.consumer_delay

        if delay > 0:
            await asyncio.sleep(delay)
