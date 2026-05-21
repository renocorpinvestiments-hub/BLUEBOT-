# ============================================================
# core/observability/diagnostics.py
# ============================================================
# Institutional Runtime Diagnostics Intelligence System
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# Lightweight runtime diagnostic primitives for:
# - event loop lag detection
# - slow execution analysis
# - starvation visibility
# - saturation visibility
# - runtime anomaly snapshots
#
# THIS FILE DOES:
# - measure runtime health signals
# - detect execution anomalies
# - expose immutable diagnostics
# - support async-safe diagnostics
# - provide low-overhead probes
#
# THIS FILE DOES NOT:
# - restart systems
# - supervise workers
# - own orchestration
# - control retries
# - manage circuits
# - own telemetry exporting
# - execute recovery logic
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - async-first
# - lock-light coordination
# - immutable snapshots
# - bounded memory usage
# - monotonic timing
# - cancellation-safe sampling
# - low allocation hot paths
# - runtime-agnostic
# - distributed-system ready
#
# ============================================================

from __future__ import annotations

import asyncio
import time

from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Deque
from typing import Dict
from typing import Mapping
from typing import Optional


# ============================================================
# Diagnostic Levels
# ============================================================

class DiagnosticLevel(str, Enum):
    """
    Runtime health severity levels.
    """

    HEALTHY = "healthy"
    WARNING = "warning"
    DEGRADED = "degraded"
    CRITICAL = "critical"


# ============================================================
# Immutable Policies
# ============================================================

@dataclass(frozen=True, slots=True)
class LoopLagPolicy:
    """
    Event loop lag detection policy.
    """

    sample_interval: float = 1.0

    warning_threshold: float = 0.050
    degraded_threshold: float = 0.150
    critical_threshold: float = 0.500

    history_size: int = 256

    enabled: bool = True

    def __post_init__(self) -> None:

        if self.sample_interval <= 0:
            raise ValueError(
                "sample_interval must be positive."
            )

        if self.warning_threshold < 0:
            raise ValueError(
                "warning_threshold cannot be negative."
            )

        if self.degraded_threshold < 0:
            raise ValueError(
                "degraded_threshold cannot be negative."
            )

        if self.critical_threshold < 0:
            raise ValueError(
                "critical_threshold cannot be negative."
            )

        if self.history_size <= 0:
            raise ValueError(
                "history_size must be positive."
            )


@dataclass(frozen=True, slots=True)
class SlowExecutionPolicy:
    """
    Slow execution detection policy.
    """

    warning_threshold: float = 1.0
    degraded_threshold: float = 5.0
    critical_threshold: float = 15.0

    history_size: int = 512

    enabled: bool = True

    def __post_init__(self) -> None:

        if self.warning_threshold < 0:
            raise ValueError(
                "warning_threshold cannot be negative."
            )

        if self.degraded_threshold < 0:
            raise ValueError(
                "degraded_threshold cannot be negative."
            )

        if self.critical_threshold < 0:
            raise ValueError(
                "critical_threshold cannot be negative."
            )

        if self.history_size <= 0:
            raise ValueError(
                "history_size must be positive."
            )


# ============================================================
# Immutable Snapshots
# ============================================================

@dataclass(frozen=True, slots=True)
class LoopLagSnapshot:
    """
    Immutable event loop lag snapshot.
    """

    level: DiagnosticLevel

    latest_lag: float
    average_lag: float
    peak_lag: float

    total_samples: int

    timestamp: float


@dataclass(frozen=True, slots=True)
class SlowExecutionSnapshot:
    """
    Immutable slow execution snapshot.
    """

    level: DiagnosticLevel

    latest_duration: float
    average_duration: float
    peak_duration: float

    total_samples: int

    timestamp: float


@dataclass(frozen=True, slots=True)
class RuntimeDiagnosticSnapshot:
    """
    Immutable combined diagnostic snapshot.
    """

    loop_lag: LoopLagSnapshot
    slow_execution: SlowExecutionSnapshot

    timestamp: float


# ============================================================
# Event Loop Lag Monitor
# ============================================================

class EventLoopLagMonitor:
    """
    Institutional-grade event loop lag detector.

    FEATURES
    --------------------------------------------------------
    - async-safe sampling
    - bounded memory usage
    - low allocation overhead
    - cancellation-safe operation
    - lock-light reads
    """

    __slots__ = (
        "_policy",
        "_samples",
        "_task",
        "_running",
        "_latest",
        "_peak",
        "_total",
        "_snapshot",
    )

    def __init__(
        self,
        policy: LoopLagPolicy = LoopLagPolicy(),
    ) -> None:

        self._policy = policy

        self._samples: Deque[float] = deque(
            maxlen=policy.history_size
        )

        self._task: Optional[asyncio.Task[None]] = None

        self._running = False

        self._latest = 0.0
        self._peak = 0.0
        self._total = 0

        self._snapshot = self._build_snapshot()

    # ========================================================
    # Lifecycle
    # ========================================================

    async def start(self) -> None:
        """
        Start lag monitoring safely.
        """

        if not self._policy.enabled:
            return

        if self._running:
            return

        self._running = True

        self._task = asyncio.create_task(
            self._monitor_loop(),
            name="diagnostics.loop_lag",
        )

    async def stop(self) -> None:
        """
        Stop lag monitoring safely.
        """

        if not self._running:
            return

        self._running = False

        if self._task is not None:

            self._task.cancel()

            with suppress(asyncio.CancelledError):
                await self._task

            self._task = None

    # ========================================================
    # Monitoring Loop
    # ========================================================

    async def _monitor_loop(self) -> None:
        """
        Lightweight lag monitoring loop.
        """

        interval = self._policy.sample_interval

        while self._running:

            started = time.monotonic()

            await asyncio.sleep(interval)

            elapsed = (
                time.monotonic() - started
            )

            lag = max(
                0.0,
                elapsed - interval,
            )

            self._record_sample(lag)

    # ========================================================
    # Recording
    # ========================================================

    def _record_sample(
        self,
        lag: float,
    ) -> None:
        """
        Record lag sample.
        """

        self._latest = lag

        if lag > self._peak:
            self._peak = lag

        self._samples.append(lag)

        self._total += 1

        self._snapshot = self._build_snapshot()

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(self) -> LoopLagSnapshot:
        """
        Lock-free immutable snapshot access.
        """

        return self._snapshot

    # ========================================================
    # Internal Helpers
    # ========================================================

    def _build_snapshot(
        self,
    ) -> LoopLagSnapshot:
        """
        Build immutable lag snapshot.
        """

        average = (
            sum(self._samples) / len(self._samples)
            if self._samples
            else 0.0
        )

        level = self._resolve_level(
            self._latest,
            self._policy.warning_threshold,
            self._policy.degraded_threshold,
            self._policy.critical_threshold,
        )

        return LoopLagSnapshot(
            level=level,
            latest_lag=self._latest,
            average_lag=average,
            peak_lag=self._peak,
            total_samples=self._total,
            timestamp=time.monotonic(),
        )

    @staticmethod
    def _resolve_level(
        value: float,
        warning: float,
        degraded: float,
        critical: float,
    ) -> DiagnosticLevel:
        """
        Resolve diagnostic severity.
        """

        if value >= critical:
            return DiagnosticLevel.CRITICAL

        if value >= degraded:
            return DiagnosticLevel.DEGRADED

        if value >= warning:
            return DiagnosticLevel.WARNING

        return DiagnosticLevel.HEALTHY


# ============================================================
# Slow Execution Monitor
# ============================================================

class SlowExecutionMonitor:
    """
    Lightweight execution duration analyzer.

    FEATURES
    --------------------------------------------------------
    - bounded memory usage
    - lock-light writes
    - monotonic timing
    - scalable runtime diagnostics
    """

    __slots__ = (
        "_policy",
        "_samples",
        "_latest",
        "_peak",
        "_total",
        "_snapshot",
    )

    def __init__(
        self,
        policy: SlowExecutionPolicy = (
            SlowExecutionPolicy()
        ),
    ) -> None:

        self._policy = policy

        self._samples: Deque[float] = deque(
            maxlen=policy.history_size
        )

        self._latest = 0.0
        self._peak = 0.0
        self._total = 0

        self._snapshot = self._build_snapshot()

    # ========================================================
    # Recording
    # ========================================================

    def record(
        self,
        duration: float,
    ) -> None:
        """
        Record execution duration safely.
        """

        if not self._policy.enabled:
            return

        if duration < 0:
            return

        self._latest = duration

        if duration > self._peak:
            self._peak = duration

        self._samples.append(duration)

        self._total += 1

        self._snapshot = self._build_snapshot()

    # ========================================================
    # Timing Helper
    # ========================================================

    def timer(self) -> float:
        """
        Return monotonic timing baseline.

        Example
        --------------------------------------------------------

        started = monitor.timer()

        ...

        monitor.record(
            time.monotonic() - started
        )
        """

        return time.monotonic()

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(
        self,
    ) -> SlowExecutionSnapshot:
        """
        Lock-free immutable snapshot access.
        """

        return self._snapshot

    # ========================================================
    # Internal Helpers
    # ========================================================

    def _build_snapshot(
        self,
    ) -> SlowExecutionSnapshot:
        """
        Build immutable snapshot.
        """

        average = (
            sum(self._samples) / len(self._samples)
            if self._samples
            else 0.0
        )

        level = self._resolve_level(
            self._latest,
            self._policy.warning_threshold,
            self._policy.degraded_threshold,
            self._policy.critical_threshold,
        )

        return SlowExecutionSnapshot(
            level=level,
            latest_duration=self._latest,
            average_duration=average,
            peak_duration=self._peak,
            total_samples=self._total,
            timestamp=time.monotonic(),
        )

    @staticmethod
    def _resolve_level(
        value: float,
        warning: float,
        degraded: float,
        critical: float,
    ) -> DiagnosticLevel:
        """
        Resolve severity level.
        """

        if value >= critical:
            return DiagnosticLevel.CRITICAL

        if value >= degraded:
            return DiagnosticLevel.DEGRADED

        if value >= warning:
            return DiagnosticLevel.WARNING

        return DiagnosticLevel.HEALTHY


# ============================================================
# Diagnostics Registry
# ============================================================

class DiagnosticsRegistry:
    """
    Centralized diagnostics coordination registry.

    PURPOSE
    --------------------------------------------------------
    - aggregate runtime diagnostics
    - expose immutable snapshots
    - provide safe empty-runtime operation

    IMPORTANT
    --------------------------------------------------------
    This registry does NOT:
    - supervise runtime
    - restart services
    - own orchestration
    - mutate execution systems
    """

    __slots__ = (
        "_loop_monitor",
        "_execution_monitor",
    )

    def __init__(
        self,
        loop_monitor: Optional[
            EventLoopLagMonitor
        ] = None,
        execution_monitor: Optional[
            SlowExecutionMonitor
        ] = None,
    ) -> None:

        self._loop_monitor = (
            loop_monitor
            or EventLoopLagMonitor()
        )

        self._execution_monitor = (
            execution_monitor
            or SlowExecutionMonitor()
        )

    # ========================================================
    # Lifecycle
    # ========================================================

    async def start(self) -> None:
        """
        Start diagnostics safely.
        """

        await self._loop_monitor.start()

    async def stop(self) -> None:
        """
        Stop diagnostics safely.
        """

        await self._loop_monitor.stop()

    # ========================================================
    # Accessors
    # ========================================================

    @property
    def loop_monitor(
        self,
    ) -> EventLoopLagMonitor:
        return self._loop_monitor

    @property
    def execution_monitor(
        self,
    ) -> SlowExecutionMonitor:
        return self._execution_monitor

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(
        self,
    ) -> RuntimeDiagnosticSnapshot:
        """
        Immutable runtime diagnostic snapshot.
        """

        return RuntimeDiagnosticSnapshot(
            loop_lag=self._loop_monitor.snapshot(),
            slow_execution=(
                self._execution_monitor.snapshot()
            ),
            timestamp=time.monotonic(),
        )


# ============================================================
# Default Diagnostics Registry
# ============================================================

DEFAULT_DIAGNOSTICS = DiagnosticsRegistry()
