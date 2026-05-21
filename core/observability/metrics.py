# ============================================================
# core/observability/metrics.py
# ============================================================
# Institutional Runtime Metrics Coordination System
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# High-performance observability measurement primitives.
#
# THIS FILE DOES:
# - aggregate lightweight runtime metrics
# - provide low-overhead counters
# - support timing + histogram measurements
# - expose immutable metric snapshots
# - provide async-safe metric coordination
# - support scalable metric registries
# - provide lock-light hot-path measurements
#
# THIS FILE DOES NOT:
# - export telemetry
# - own dashboards
# - send Prometheus metrics
# - supervise runtime systems
# - restart services
# - manage orchestration
# - execute workloads
# - own tracing
# - own diagnostics
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - async-first
# - lock-light hot paths
# - immutable snapshots
# - deterministic metric accounting
# - low allocation overhead
# - zero runtime coupling
# - distributed-system ready
# - safe empty-runtime behavior
# - bounded memory usage
# - scalable registry architecture
#
# ============================================================

from __future__ import annotations

import time

from collections import deque
from dataclasses import dataclass
from statistics import mean
from threading import Lock
from types import MappingProxyType
from typing import Deque
from typing import Dict
from typing import Iterable
from typing import Mapping
from typing import Optional


# ============================================================
# Metric Snapshot Models
# ============================================================

@dataclass(frozen=True, slots=True)
class CounterSnapshot:
    """
    Immutable counter metric snapshot.

    Safe for:
    - diagnostics
    - observability
    - orchestration
    - dashboards
    - tracing systems
    """

    name: str
    value: int
    updated_at: float


@dataclass(frozen=True, slots=True)
class TimingSnapshot:
    """
    Immutable timing metric snapshot.

    Stores lightweight aggregated timing data.
    """

    name: str

    count: int

    minimum: float
    maximum: float
    average: float
    latest: float

    updated_at: float


@dataclass(frozen=True, slots=True)
class HistogramSnapshot:
    """
    Immutable histogram snapshot.

    Uses bounded samples for:
    - latency analysis
    - retry delay visibility
    - queue wait analysis
    """

    name: str

    samples: int

    minimum: float
    maximum: float
    average: float

    updated_at: float


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """
    Immutable full metrics registry snapshot.

    Safe for:
    - API exposure
    - orchestration reads
    - diagnostics
    - runtime introspection
    """

    counters: Mapping[str, CounterSnapshot]

    timings: Mapping[str, TimingSnapshot]

    histograms: Mapping[str, HistogramSnapshot]

    timestamp: float


# ============================================================
# Counter Primitive
# ============================================================

class Counter:
    """
    High-performance counter primitive.

    DESIGN GOALS
    --------------------------------------------------------
    - lock-light writes
    - deterministic increments
    - low allocation overhead
    - thread-safe accounting
    """

    __slots__ = (
        "_name",
        "_value",
        "_updated_at",
        "_lock",
    )

    def __init__(
        self,
        name: str,
    ) -> None:

        self._name = name

        self._value: int = 0

        self._updated_at = time.monotonic()

        self._lock = Lock()

    # ========================================================
    # Operations
    # ========================================================

    def increment(
        self,
        value: int = 1,
    ) -> int:
        """
        Increment counter safely.
        """

        if value < 0:
            raise ValueError(
                "Counter increment cannot be negative."
            )

        with self._lock:

            self._value += value

            self._updated_at = time.monotonic()

            return self._value

    def decrement(
        self,
        value: int = 1,
    ) -> int:
        """
        Decrement counter safely.
        """

        if value < 0:
            raise ValueError(
                "Counter decrement cannot be negative."
            )

        with self._lock:

            self._value -= value

            self._updated_at = time.monotonic()

            return self._value

    def reset(self) -> None:
        """
        Reset counter safely.
        """

        with self._lock:

            self._value = 0

            self._updated_at = time.monotonic()

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(self) -> CounterSnapshot:
        """
        Immutable counter snapshot.
        """

        return CounterSnapshot(
            name=self._name,
            value=self._value,
            updated_at=self._updated_at,
        )


# ============================================================
# Timing Primitive
# ============================================================

class TimingMetric:
    """
    Lightweight timing aggregation primitive.

    FEATURES
    --------------------------------------------------------
    - monotonic timing
    - bounded memory
    - low allocation overhead
    - fast aggregation reads
    """

    __slots__ = (
        "_name",
        "_count",
        "_total",
        "_minimum",
        "_maximum",
        "_latest",
        "_updated_at",
        "_lock",
    )

    def __init__(
        self,
        name: str,
    ) -> None:

        self._name = name

        self._count = 0

        self._total = 0.0

        self._minimum = float("inf")

        self._maximum = 0.0

        self._latest = 0.0

        self._updated_at = time.monotonic()

        self._lock = Lock()

    # ========================================================
    # Recording
    # ========================================================

    def record(
        self,
        duration: float,
    ) -> None:
        """
        Record timing duration safely.
        """

        if duration < 0:
            raise ValueError(
                "Duration cannot be negative."
            )

        with self._lock:

            self._count += 1

            self._total += duration

            self._latest = duration

            self._minimum = min(
                self._minimum,
                duration,
            )

            self._maximum = max(
                self._maximum,
                duration,
            )

            self._updated_at = time.monotonic()

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(self) -> TimingSnapshot:
        """
        Immutable timing snapshot.
        """

        count = self._count

        average = (
            self._total / count
            if count > 0
            else 0.0
        )

        minimum = (
            self._minimum
            if count > 0
            else 0.0
        )

        return TimingSnapshot(
            name=self._name,
            count=count,
            minimum=minimum,
            maximum=self._maximum,
            average=average,
            latest=self._latest,
            updated_at=self._updated_at,
        )


# ============================================================
# Histogram Primitive
# ============================================================

class HistogramMetric:
    """
    Bounded histogram metric.

    DESIGN GOALS
    --------------------------------------------------------
    - bounded memory
    - lightweight sampling
    - scalable aggregation
    - allocation control
    """

    __slots__ = (
        "_name",
        "_samples",
        "_max_samples",
        "_updated_at",
        "_lock",
    )

    def __init__(
        self,
        name: str,
        max_samples: int = 1024,
    ) -> None:

        if max_samples <= 0:
            raise ValueError(
                "max_samples must be positive."
            )

        self._name = name

        self._samples: Deque[float] = deque(
            maxlen=max_samples,
        )

        self._max_samples = max_samples

        self._updated_at = time.monotonic()

        self._lock = Lock()

    # ========================================================
    # Recording
    # ========================================================

    def record(
        self,
        value: float,
    ) -> None:
        """
        Record histogram value safely.
        """

        if value < 0:
            raise ValueError(
                "Histogram value cannot be negative."
            )

        with self._lock:

            self._samples.append(value)

            self._updated_at = time.monotonic()

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(self) -> HistogramSnapshot:
        """
        Immutable histogram snapshot.
        """

        samples = tuple(self._samples)

        if not samples:

            return HistogramSnapshot(
                name=self._name,
                samples=0,
                minimum=0.0,
                maximum=0.0,
                average=0.0,
                updated_at=self._updated_at,
            )

        return HistogramSnapshot(
            name=self._name,
            samples=len(samples),
            minimum=min(samples),
            maximum=max(samples),
            average=mean(samples),
            updated_at=self._updated_at,
        )


# ============================================================
# Metrics Registry
# ============================================================

class MetricsRegistry:
    """
    Institutional-grade metrics registry.

    FEATURES
    --------------------------------------------------------
    - lock-light metric registration
    - scalable metric lookup
    - immutable snapshots
    - safe empty-runtime behavior
    - low overhead hot paths

    IMPORTANT
    --------------------------------------------------------
    This registry ONLY stores measurements.

    It does NOT:
    - export telemetry
    - push metrics externally
    - own monitoring systems
    """

    __slots__ = (
        "_counters",
        "_timings",
        "_histograms",
        "_lock",
    )

    def __init__(self) -> None:

        self._counters: Dict[
            str,
            Counter,
        ] = {}

        self._timings: Dict[
            str,
            TimingMetric,
        ] = {}

        self._histograms: Dict[
            str,
            HistogramMetric,
        ] = {}

        self._lock = Lock()

    # ========================================================
    # Counter Operations
    # ========================================================

    def increment(
        self,
        name: str,
        value: int = 1,
    ) -> int:
        """
        Increment counter metric.
        """

        counter = self._get_or_create_counter(
            name,
        )

        return counter.increment(value)

    def decrement(
        self,
        name: str,
        value: int = 1,
    ) -> int:
        """
        Decrement counter metric.
        """

        counter = self._get_or_create_counter(
            name,
        )

        return counter.decrement(value)

    # ========================================================
    # Timing Operations
    # ========================================================

    def record_timing(
        self,
        name: str,
        duration: float,
    ) -> None:
        """
        Record timing metric.
        """

        metric = self._get_or_create_timing(
            name,
        )

        metric.record(duration)

    # ========================================================
    # Histogram Operations
    # ========================================================

    def record_histogram(
        self,
        name: str,
        value: float,
    ) -> None:
        """
        Record histogram value.
        """

        metric = self._get_or_create_histogram(
            name,
        )

        metric.record(value)

    # ========================================================
    # Timer Context
    # ========================================================

    class _TimerContext:
        """
        Lightweight monotonic timer context.
        """

        __slots__ = (
            "_registry",
            "_name",
            "_started",
        )

        def __init__(
            self,
            registry: MetricsRegistry,
            name: str,
        ) -> None:

            self._registry = registry

            self._name = name

            self._started = 0.0

        def __enter__(self) -> "_TimerContext":

            self._started = time.monotonic()

            return self

        def __exit__(
            self,
            exc_type,
            exc,
            tb,
        ) -> None:

            duration = (
                time.monotonic()
                - self._started
            )

            self._registry.record_timing(
                self._name,
                duration,
            )

    def timer(
        self,
        name: str,
    ) -> "_TimerContext":
        """
        Lightweight timer helper.

        Example
        ----------------------------------------------------
        with metrics.timer("queue.dispatch"):
            ...
        """

        return self._TimerContext(
            self,
            name,
        )

    # ========================================================
    # Metric Lookup
    # ========================================================

    def counter(
        self,
        name: str,
    ) -> Optional[Counter]:
        """
        Safe counter lookup.
        """

        return self._counters.get(
            name.strip().lower(),
        )

    def timing(
        self,
        name: str,
    ) -> Optional[TimingMetric]:
        """
        Safe timing lookup.
        """

        return self._timings.get(
            name.strip().lower(),
        )

    def histogram(
        self,
        name: str,
    ) -> Optional[HistogramMetric]:
        """
        Safe histogram lookup.
        """

        return self._histograms.get(
            name.strip().lower(),
        )

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(self) -> MetricsSnapshot:
        """
        Immutable registry snapshot.

        SAFE WHEN EMPTY
        --------------------------------------------------------
        Returns fully valid immutable snapshot
        even when no metrics exist.
        """

        counters = MappingProxyType({
            name: metric.snapshot()
            for name, metric
            in self._counters.items()
        })

        timings = MappingProxyType({
            name: metric.snapshot()
            for name, metric
            in self._timings.items()
        })

        histograms = MappingProxyType({
            name: metric.snapshot()
            for name, metric
            in self._histograms.items()
        })

        return MetricsSnapshot(
            counters=counters,
            timings=timings,
            histograms=histograms,
            timestamp=time.monotonic(),
        )

    # ========================================================
    # Internal Registry Helpers
    # ========================================================

    def _get_or_create_counter(
        self,
        name: str,
    ) -> Counter:

        normalized = self._normalize(name)

        metric = self._counters.get(normalized)

        if metric is not None:
            return metric

        with self._lock:

            metric = self._counters.get(normalized)

            if metric is None:

                metric = Counter(normalized)

                self._counters[normalized] = metric

            return metric

    def _get_or_create_timing(
        self,
        name: str,
    ) -> TimingMetric:

        normalized = self._normalize(name)

        metric = self._timings.get(normalized)

        if metric is not None:
            return metric

        with self._lock:

            metric = self._timings.get(normalized)

            if metric is None:

                metric = TimingMetric(normalized)

                self._timings[normalized] = metric

            return metric

    def _get_or_create_histogram(
        self,
        name: str,
    ) -> HistogramMetric:

        normalized = self._normalize(name)

        metric = self._histograms.get(normalized)

        if metric is not None:
            return metric

        with self._lock:

            metric = self._histograms.get(normalized)

            if metric is None:

                metric = HistogramMetric(normalized)

                self._histograms[normalized] = metric

            return metric

    # ========================================================
    # Normalization
    # ========================================================

    @staticmethod
    def _normalize(
        name: str,
    ) -> str:
        """
        Deterministic metric normalization.
        """

        normalized = (
            name.strip().lower()
        )

        if not normalized:
            raise ValueError(
                "Metric name cannot be empty."
            )

        return normalized


# ============================================================
# Default Global Registry
# ============================================================

DEFAULT_METRICS = MetricsRegistry()
