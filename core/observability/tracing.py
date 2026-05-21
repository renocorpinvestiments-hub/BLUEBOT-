# ============================================================
# core/observability/tracing.py
# ============================================================
# Institutional Distributed Tracing Coordination System
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# High-performance async-safe execution tracing primitives.
#
# THIS FILE DOES:
# - coordinate distributed execution tracing
# - provide async-safe context propagation
# - manage trace/span relationships
# - expose immutable tracing snapshots
# - support sync + async tracing
# - provide correlation identifiers
# - track execution duration safely
#
# THIS FILE DOES NOT:
# - own logging
# - export telemetry
# - supervise workloads
# - restart systems
# - execute orchestration
# - manage queues
# - own diagnostics
# - import runtime internals
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - async-first
# - contextvars-based propagation
# - immutable trace snapshots
# - lock-free hot paths
# - deterministic trace relationships
# - low allocation overhead
# - distributed-system ready
# - zero runtime coupling
# - safe empty-runtime behavior
# - scalable trace coordination
#
# ============================================================

from __future__ import annotations

import contextvars
import inspect
import secrets
import time

from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any
from typing import AsyncIterator
from typing import Callable
from typing import Dict
from typing import Mapping
from typing import Optional
from typing import TypeVar


# ============================================================
# Generics
# ============================================================

T = TypeVar("T")


# ============================================================
# Trace State
# ============================================================

class TraceStatus(str, Enum):
    """
    Immutable trace execution status.
    """

    ACTIVE = "active"

    COMPLETED = "completed"

    FAILED = "failed"


# ============================================================
# Context Variables
# ============================================================

_CURRENT_TRACE_ID: contextvars.ContextVar[
    Optional[str]
] = contextvars.ContextVar(
    "current_trace_id",
    default=None,
)

_CURRENT_SPAN_ID: contextvars.ContextVar[
    Optional[str]
] = contextvars.ContextVar(
    "current_span_id",
    default=None,
)


# ============================================================
# Immutable Snapshots
# ============================================================

@dataclass(frozen=True, slots=True)
class TraceSpanSnapshot:
    """
    Immutable span snapshot.

    Safe for:
    - observability
    - diagnostics
    - orchestration
    - debugging
    - distributed tracing systems
    """

    trace_id: str

    span_id: str

    parent_span_id: Optional[str]

    operation: str

    status: TraceStatus

    started_at: float

    finished_at: Optional[float]

    duration: Optional[float]

    metadata: Mapping[str, Any]

    error: Optional[str]


@dataclass(frozen=True, slots=True)
class TraceSnapshot:
    """
    Immutable trace snapshot.
    """

    trace_id: str

    spans: Mapping[str, TraceSpanSnapshot]

    created_at: float


# ============================================================
# Trace Span
# ============================================================

class TraceSpan:
    """
    Institutional-grade trace span.

    FEATURES
    --------------------------------------------------------
    - async-safe propagation
    - immutable snapshots
    - distributed tracing support
    - deterministic parent relationships
    - low-overhead execution tracking

    IMPORTANT
    --------------------------------------------------------
    This class ONLY provides tracing primitives.

    It does NOT:
    - own logging
    - own telemetry export
    - control execution
    """

    __slots__ = (
        "_trace_id",
        "_span_id",
        "_parent_span_id",
        "_operation",
        "_started_at",
        "_finished_at",
        "_status",
        "_metadata",
        "_error",
    )

    def __init__(
        self,
        *,
        trace_id: str,
        span_id: str,
        parent_span_id: Optional[str],
        operation: str,
        metadata: Optional[
            Mapping[str, Any]
        ] = None,
    ) -> None:

        normalized = operation.strip().lower()

        if not normalized:
            raise ValueError(
                "Operation name cannot be empty."
            )

        self._trace_id = trace_id

        self._span_id = span_id

        self._parent_span_id = parent_span_id

        self._operation = normalized

        self._started_at = time.monotonic()

        self._finished_at: Optional[
            float
        ] = None

        self._status = TraceStatus.ACTIVE

        self._metadata = (
            MappingProxyType(dict(metadata))
            if metadata
            else MappingProxyType({})
        )

        self._error: Optional[str] = None

    # ========================================================
    # Properties
    # ========================================================

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def span_id(self) -> str:
        return self._span_id

    @property
    def parent_span_id(
        self,
    ) -> Optional[str]:
        return self._parent_span_id

    @property
    def operation(self) -> str:
        return self._operation

    @property
    def status(self) -> TraceStatus:
        return self._status

    # ========================================================
    # Lifecycle
    # ========================================================

    def complete(self) -> None:
        """
        Mark span as completed.

        Idempotent operation.
        """

        if (
            self._status
            != TraceStatus.ACTIVE
        ):
            return

        self._finished_at = time.monotonic()

        self._status = TraceStatus.COMPLETED

    def fail(
        self,
        error: Exception,
    ) -> None:
        """
        Mark span as failed.

        Idempotent operation.
        """

        if (
            self._status
            != TraceStatus.ACTIVE
        ):
            return

        self._finished_at = time.monotonic()

        self._status = TraceStatus.FAILED

        self._error = repr(error)

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(
        self,
    ) -> TraceSpanSnapshot:
        """
        Immutable span snapshot.
        """

        duration: Optional[float] = None

        if self._finished_at is not None:

            duration = (
                self._finished_at
                - self._started_at
            )

        return TraceSpanSnapshot(
            trace_id=self._trace_id,
            span_id=self._span_id,
            parent_span_id=self._parent_span_id,
            operation=self._operation,
            status=self._status,
            started_at=self._started_at,
            finished_at=self._finished_at,
            duration=duration,
            metadata=self._metadata,
            error=self._error,
        )


# ============================================================
# Trace Registry
# ============================================================

class TraceRegistry:
    """
    Lightweight trace registry.

    PURPOSE
    --------------------------------------------------------
    - centralized trace visibility
    - immutable trace snapshots
    - scalable trace coordination

    IMPORTANT
    --------------------------------------------------------
    This registry does NOT:
    - export telemetry
    - own runtime execution
    - supervise systems
    """

    __slots__ = (
        "_traces",
    )

    def __init__(self) -> None:

        self._traces: Dict[
            str,
            Dict[str, TraceSpan]
        ] = {}

    # ========================================================
    # Registration
    # ========================================================

    def register(
        self,
        span: TraceSpan,
    ) -> None:
        """
        Register trace span safely.
        """

        trace = self._traces.setdefault(
            span.trace_id,
            {},
        )

        trace[span.span_id] = span

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(
        self,
    ) -> Mapping[str, TraceSnapshot]:
        """
        Immutable registry snapshot.

        SAFE WHEN EMPTY
        --------------------------------------------------------
        Returns valid immutable mappings even
        when no traces exist.
        """

        snapshots: Dict[
            str,
            TraceSnapshot,
        ] = {}

        for trace_id, spans in (
            self._traces.items()
        ):

            snapshots[trace_id] = TraceSnapshot(
                trace_id=trace_id,
                spans=MappingProxyType({
                    span_id: span.snapshot()
                    for span_id, span
                    in spans.items()
                }),
                created_at=min(
                    span.snapshot().started_at
                    for span in spans.values()
                ),
            )

        return MappingProxyType(snapshots)


# ============================================================
# Tracing Manager
# ============================================================

class TracingManager:
    """
    Institutional distributed tracing manager.

    FEATURES
    --------------------------------------------------------
    - async-safe context propagation
    - sync + async compatibility
    - deterministic trace hierarchy
    - scalable tracing coordination
    - low allocation hot paths

    IMPORTANT
    --------------------------------------------------------
    This manager ONLY coordinates tracing.

    It does NOT:
    - own telemetry exporting
    - own logging systems
    - control execution
    """

    __slots__ = (
        "_registry",
    )

    def __init__(
        self,
        registry: Optional[
            TraceRegistry
        ] = None,
    ) -> None:

        self._registry = (
            registry
            if registry is not None
            else TraceRegistry()
        )

    # ========================================================
    # Context Access
    # ========================================================

    @staticmethod
    def current_trace_id() -> Optional[str]:
        """
        Return active trace ID safely.
        """

        return _CURRENT_TRACE_ID.get()

    @staticmethod
    def current_span_id() -> Optional[str]:
        """
        Return active span ID safely.
        """

        return _CURRENT_SPAN_ID.get()

    # ========================================================
    # Span Context
    # ========================================================

    @asynccontextmanager
    async def span(
        self,
        operation: str,
        *,
        metadata: Optional[
            Mapping[str, Any]
        ] = None,
        trace_id: Optional[str] = None,
    ) -> AsyncIterator[TraceSpan]:
        """
        Async-safe trace span context.

        Example
        ----------------------------------------------------
        async with tracing.span(
            "queue.dispatch"
        ):
            ...

        FEATURES
        ----------------------------------------------------
        - async-safe propagation
        - automatic parent linking
        - deterministic trace hierarchy
        - cancellation-safe cleanup
        """

        current_trace_id = (
            trace_id
            or self.current_trace_id()
            or self._generate_id()
        )

        parent_span_id = (
            self.current_span_id()
        )

        span_id = self._generate_id()

        span = TraceSpan(
            trace_id=current_trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            operation=operation,
            metadata=metadata,
        )

        self._registry.register(span)

        trace_token = (
            _CURRENT_TRACE_ID.set(
                current_trace_id
            )
        )

        span_token = (
            _CURRENT_SPAN_ID.set(span_id)
        )

        try:

            yield span

            span.complete()

        except Exception as exc:

            span.fail(exc)

            raise

        finally:

            _CURRENT_TRACE_ID.reset(
                trace_token
            )

            _CURRENT_SPAN_ID.reset(
                span_token
            )

    # ========================================================
    # Execution Helpers
    # ========================================================

    async def trace(
        self,
        operation: str,
        callback: Callable[..., Any],
        *args: Any,
        metadata: Optional[
            Mapping[str, Any]
        ] = None,
        **kwargs: Any,
    ) -> T:
        """
        Execute callback inside trace span.

        FEATURES
        ----------------------------------------------------
        - sync + async support
        - distributed tracing support
        - cancellation propagation
        """

        async with self.span(
            operation,
            metadata=metadata,
        ):

            result = callback(
                *args,
                **kwargs,
            )

            if inspect.isawaitable(result):
                return await result

            return result

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(
        self,
    ) -> Mapping[str, TraceSnapshot]:
        """
        Immutable tracing snapshot.
        """

        return self._registry.snapshot()

    # ========================================================
    # Internal Helpers
    # ========================================================

    @staticmethod
    def _generate_id() -> str:
        """
        Generate distributed-safe identifier.

        Uses:
        - cryptographically strong randomness
        - low collision probability
        - allocation-light generation
        """

        return secrets.token_hex(16)


# ============================================================
# Default Global Tracing Manager
# ============================================================

DEFAULT_TRACING = TracingManager()
