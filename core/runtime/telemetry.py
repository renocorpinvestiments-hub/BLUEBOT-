# ============================================================
# core/runtime/telemetry.py
# ============================================================
# Institutional Runtime Telemetry Engine
# ============================================================
#
# RESPONSIBILITIES
# ------------------------------------------------------------
# - runtime observability
# - async-safe telemetry collection
# - latency tracking
# - throughput tracking
# - tracing foundation
# - rolling analytics
# - runtime diagnostics
# - exporter-ready metrics model
#
# DESIGN GOALS
# ------------------------------------------------------------
# - async-first
# - low-overhead
# - scalable
# - lock-efficient
# - production-safe
# - broker compatible
# - plugin-safe
# - event-driven
# - microservice-ready
# - OpenTelemetry compatible later
#
# IMPORTANT
# ------------------------------------------------------------
# THIS FILE MUST NOT:
# - depend on Prometheus
# - depend on Datadog
# - depend on FastAPI
# - contain business logic
# - know infrastructure vendors
#
# THIS FILE IS:
# - internal telemetry abstraction
# - runtime observability layer
# - diagnostics infrastructure
#
# ============================================================

from __future__ import annotations

import asyncio
import statistics
import time
import uuid

from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    AsyncIterator,
    Deque,
    Dict,
    List,
    Optional,
)


# ============================================================
# METRIC TYPES
# ============================================================

class MetricType(str, Enum):
    """
    Supported telemetry metric types.
    """

    COUNTER = "counter"

    GAUGE = "gauge"

    TIMING = "timing"

    THROUGHPUT = "throughput"


# ============================================================
# TRACE CONTEXT
# ============================================================

@dataclass(slots=True)
class TraceContext:
    """
    Distributed trace context.

    Future-compatible with:
    - OpenTelemetry
    - Jaeger
    - Kafka tracing
    - distributed brokers
    - microservices
    """

    trace_id: str

    span_id: str

    parent_span_id: Optional[str] = None

    created_at: float = field(
        default_factory=time.time
    )

    metadata: Dict[str, Any] = field(
        default_factory=dict
    )

    @classmethod
    def create(
        cls,
        *,
        parent_span_id: Optional[str] = None,
    ) -> "TraceContext":

        return cls(
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex,
            parent_span_id=parent_span_id,
        )

    def child(self) -> "TraceContext":
        """
        Create child trace span.
        """

        return TraceContext(
            trace_id=self.trace_id,
            span_id=uuid.uuid4().hex,
            parent_span_id=self.span_id,
        )


# ============================================================
# TELEMETRY METRIC
# ============================================================

@dataclass(slots=True)
class TelemetryMetric:
    """
    Metric metadata model.
    """

    name: str

    metric_type: MetricType

    description: Optional[str] = None

    tags: Dict[str, str] = field(
        default_factory=dict
    )

    created_at: float = field(
        default_factory=time.time
    )


# ============================================================
# ROLLING WINDOW
# ============================================================

class RollingWindow:
    """
    High-performance rolling metric window.

    Used for:
    - latency windows
    - throughput windows
    - burst detection
    - p95/p99 calculations later
    - autoscaling intelligence
    """

    def __init__(
        self,
        *,
        max_samples: int = 10_000,
    ) -> None:

        self._values: Deque[float] = deque(
            maxlen=max_samples
        )

    def add(
        self,
        value: float,
    ) -> None:

        self._values.append(value)

    def values(self) -> List[float]:

        return list(self._values)

    def count(self) -> int:

        return len(self._values)

    def average(self) -> float:

        if not self._values:
            return 0.0

        return statistics.fmean(self._values)

    def minimum(self) -> float:

        if not self._values:
            return 0.0

        return min(self._values)

    def maximum(self) -> float:

        if not self._values:
            return 0.0

        return max(self._values)

    def percentile(
        self,
        percentile: float,
    ) -> float:
        """
        Percentile calculation.

        Example:
        - p95
        - p99
        """

        if not self._values:
            return 0.0

        sorted_values = sorted(self._values)

        index = int(
            (percentile / 100)
            * (len(sorted_values) - 1)
        )

        return sorted_values[index]

    def snapshot(self) -> Dict[str, Any]:

        return {
            "count": self.count(),
            "avg": round(self.average(), 4),
            "min": round(self.minimum(), 4),
            "max": round(self.maximum(), 4),
            "p95": round(
                self.percentile(95),
                4,
            ),
            "p99": round(
                self.percentile(99),
                4,
            ),
        }


# ============================================================
# RUNTIME TELEMETRY ENGINE
# ============================================================

class RuntimeTelemetry:
    """
    Institutional-grade telemetry runtime.

    Responsibilities:
    --------------------------------------------------------
    - metrics
    - tracing
    - latency tracking
    - throughput tracking
    - diagnostics
    - runtime observability
    - exporter abstraction

    Architecture:
    --------------------------------------------------------
    Designed for:
    - async runtimes
    - event-driven systems
    - distributed brokers
    - plugin ecosystems
    - future microservices
    """

    def __init__(self) -> None:

        # ====================================================
        # STRUCTURAL LOCK
        # ====================================================

        self._lock = asyncio.Lock()

        # ====================================================
        # METRIC DEFINITIONS
        # ====================================================

        self._metrics: Dict[
            str,
            TelemetryMetric,
        ] = {}

        # ====================================================
        # COUNTERS
        # ====================================================

        self._counters: Dict[
            str,
            int,
        ] = {}

        # ====================================================
        # GAUGES
        # ====================================================

        self._gauges: Dict[
            str,
            float,
        ] = {}

        # ====================================================
        # LATENCY WINDOWS
        # ====================================================

        self._timings: Dict[
            str,
            RollingWindow,
        ] = {}

        # ====================================================
        # THROUGHPUT WINDOWS
        # ====================================================

        self._throughput: Dict[
            str,
            Deque[float],
        ] = {}

        # ====================================================
        # TRACE STORAGE
        # ====================================================

        self._active_traces: Dict[
            str,
            TraceContext,
        ] = {}

        # ====================================================
        # CREATED
        # ====================================================

        self._created_at = time.time()

    # ========================================================
    # METRIC REGISTRATION
    # ========================================================

    async def register_metric(
        self,
        name: str,
        metric_type: MetricType,
        *,
        description: Optional[str] = None,
        tags: Optional[
            Dict[str, str]
        ] = None,
    ) -> None:

        async with self._lock:

            if name in self._metrics:
                return

            self._metrics[name] = TelemetryMetric(
                name=name,
                metric_type=metric_type,
                description=description,
                tags=tags or {},
            )

    # ========================================================
    # COUNTERS
    # ========================================================

    async def increment(
        self,
        name: str,
        value: int = 1,
    ) -> None:
        """
        Increment monotonic counter.

        Examples:
        - events processed
        - retries
        - failures
        """

        self._counters[name] = (
            self._counters.get(name, 0)
            + value
        )

    async def counter(
        self,
        name: str,
    ) -> int:

        return self._counters.get(name, 0)

    # ========================================================
    # GAUGES
    # ========================================================

    async def gauge(
        self,
        name: str,
        value: float,
    ) -> None:
        """
        Set current gauge value.

        Examples:
        - queue depth
        - active workers
        - memory pressure
        """

        self._gauges[name] = value

    async def gauge_value(
        self,
        name: str,
    ) -> float:

        return self._gauges.get(name, 0.0)

    # ========================================================
    # TIMINGS
    # ========================================================

    async def timing(
        self,
        name: str,
        duration_ms: float,
    ) -> None:
        """
        Record timing metric.

        Uses rolling windows for:
        - p95
        - p99
        - anomaly detection
        """

        if name not in self._timings:

            async with self._lock:

                if name not in self._timings:
                    self._timings[name] = (
                        RollingWindow()
                    )

        self._timings[name].add(
            duration_ms
        )

    async def timing_snapshot(
        self,
        name: str,
    ) -> Dict[str, Any]:

        window = self._timings.get(name)

        if not window:
            return {}

        return window.snapshot()

    # ========================================================
    # THROUGHPUT
    # ========================================================

    async def record_throughput(
        self,
        name: str,
    ) -> None:
        """
        Records timestamp for throughput analysis.

        Enables:
        - events/sec
        - handlers/sec
        - worker/sec
        """

        if name not in self._throughput:

            async with self._lock:

                if name not in self._throughput:
                    self._throughput[name] = deque(
                        maxlen=10_000
                    )

        self._throughput[name].append(
            time.time()
        )

    async def throughput(
        self,
        name: str,
        *,
        seconds: int = 60,
    ) -> float:
        """
        Calculate rolling throughput.

        Example:
        events/sec over last 60s
        """

        timestamps = self._throughput.get(name)

        if not timestamps:
            return 0.0

        now = time.time()

        count = sum(
            1
            for ts in timestamps
            if now - ts <= seconds
        )

        return round(
            count / max(seconds, 1),
            4,
        )

    # ========================================================
    # TRACE MANAGEMENT
    # ========================================================

    async def create_trace(
        self,
        *,
        parent_span_id: Optional[str] = None,
    ) -> TraceContext:

        trace = TraceContext.create(
            parent_span_id=parent_span_id
        )

        self._active_traces[
            trace.span_id
        ] = trace

        return trace

    async def finish_trace(
        self,
        span_id: str,
    ) -> None:

        self._active_traces.pop(
            span_id,
            None,
        )

    # ========================================================
    # ASYNC TIMING CONTEXT
    # ========================================================

    @asynccontextmanager
    async def track(
        self,
        name: str,
    ) -> AsyncIterator[None]:
        """
        Async timing context manager.

        Example:
        ----------------------------------------------------
        async with telemetry.track(
            "event.dispatch"
        ):
            await dispatch_event()
        """

        start = time.perf_counter()

        try:
            yield

        finally:

            duration_ms = (
                time.perf_counter() - start
            ) * 1000

            await self.timing(
                name,
                duration_ms,
            )

    # ========================================================
    # SNAPSHOT
    # ========================================================

    async def snapshot(self) -> Dict[str, Any]:
        """
        Immutable telemetry snapshot.

        Useful for:
        - dashboards
        - diagnostics
        - orchestration
        - autoscaling
        """

        async with self._lock:

            return {
                "uptime": round(
                    time.time()
                    - self._created_at,
                    4,
                ),
                "metrics": {
                    name: {
                        "type": metric.metric_type.value,
                        "description": (
                            metric.description
                        ),
                        "tags": dict(metric.tags),
                    }
                    for (
                        name,
                        metric,
                    ) in self._metrics.items()
                },
                "counters": dict(
                    self._counters
                ),
                "gauges": dict(
                    self._gauges
                ),
                "timings": {
                    name: window.snapshot()
                    for (
                        name,
                        window,
                    ) in self._timings.items()
                },
                "throughput": {
                    name: await self.throughput(
                        name,
                    )
                    for name in self._throughput
                },
                "active_traces": len(
                    self._active_traces
                ),
            }

    # ========================================================
    # RESET
    # ========================================================

    async def reset(self) -> None:
        """
        Safe telemetry reset.

        Useful for:
        - tests
        - isolated runtimes
        - worker recycling
        """

        async with self._lock:

            self._counters.clear()

            self._gauges.clear()

            self._timings.clear()

            self._throughput.clear()

            self._active_traces.clear()


# ============================================================
# GLOBAL SINGLETON
# ============================================================

_runtime_telemetry: Optional[
    RuntimeTelemetry
] = None

_runtime_telemetry_lock = asyncio.Lock()


async def get_telemetry() -> RuntimeTelemetry:
    """
    Async-safe singleton accessor.

    Future-compatible with:
    - distributed coordinators
    - clustered runtimes
    - broker replication
    """

    global _runtime_telemetry

    if _runtime_telemetry is None:

        async with _runtime_telemetry_lock:

            if _runtime_telemetry is None:

                _runtime_telemetry = (
                    RuntimeTelemetry()
                )

    return _runtime_telemetry
