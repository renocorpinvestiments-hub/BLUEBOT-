"""
RENOCORP CORE SYSTEM MIDDLEWARE
===============================

Institutional-grade execution infrastructure middleware.

Responsibilities:
- correlation injection
- trace propagation
- request timing
- retry wrapping
- timeout enforcement
- backpressure guarding
- load shedding
- queue protection
- transaction boundary wrapping
- logging hooks
- observability hooks
- execution enrichment

DOES NOT:
- own telemetry systems
- calculate metrics
- store traces
- manage runtime orchestration
- implement queue systems
- own retry engines
- implement business logic
- manage authentication
- manage authorization

Architecture Boundaries:
middleware/system.py = execution infrastructure interception
observability/       = telemetry analytics + aggregation
runtime/             = orchestration authority
queue/               = delivery infrastructure
resilience/          = retry/circuit/load policy ownership
logger/              = transport + persistence
security/            = auth + crypto policy ownership

Design Goals:
- async-first
- ultra-lightweight
- deterministic execution
- minimal allocations
- zero blocking I/O
- cancellation-safe
- timeout-safe
- idempotent middleware behavior
- context-safe propagation
- fault isolation
- composable interception
- scalable infrastructure hooks
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
)

# ============================================================
# OPTIONAL INTEGRATIONS
# ============================================================

try:
    from core.logger.manager import LoggerManager  # type: ignore
except Exception:  # pragma: no cover
    LoggerManager = Any

try:
    from core.resilience import RetryPolicy  # type: ignore
except Exception:  # pragma: no cover
    RetryPolicy = Any

# ============================================================
# CONSTANTS
# ============================================================

DEFAULT_CORRELATION_KEY = "correlation_id"
DEFAULT_TRACE_KEY = "trace_id"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_LOAD_THRESHOLD = 10_000
DEFAULT_CONTEXT_KEY = "system"

# ============================================================
# EXCEPTIONS
# ============================================================


class SystemMiddlewareError(RuntimeError):
    """Base system middleware error."""


class RequestTimeoutError(SystemMiddlewareError):
    """Execution timeout exceeded."""


class BackpressureExceededError(SystemMiddlewareError):
    """Backpressure threshold exceeded."""


class LoadSheddingError(SystemMiddlewareError):
    """Request rejected due to overload."""


# ============================================================
# IMMUTABLE CONTEXT
# ============================================================


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Immutable execution metadata."""

    correlation_id: str
    trace_id: str
    started_at: float
    deadline: Optional[float]
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True, slots=True)
class ExecutionMetrics:
    """Immutable execution timing snapshot."""

    started_at: float
    completed_at: float
    duration_ms: float
    success: bool


# ============================================================
# OBSERVABILITY HOOKS
# ============================================================


class ObservabilityHook(Protocol):
    async def emit(
        self,
        event: Mapping[str, Any],
    ) -> None:
        ...


# ============================================================
# BASE MIDDLEWARE
# ============================================================


class BaseSystemMiddleware(abc.ABC):
    """
    Base execution infrastructure middleware.

    Compatible with middleware/base.py.

    Intentionally avoids:
    - runtime orchestration
    - telemetry ownership
    - resilience policy ownership
    - queue ownership
    - logging persistence
    """

    @abc.abstractmethod
    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        ...


# ============================================================
# CORRELATION MIDDLEWARE
# ============================================================


class CorrelationMiddleware(BaseSystemMiddleware):
    """
    Correlation and trace propagation middleware.

    Lightweight.
    Deterministic.
    Immutable context injection.
    """

    def __init__(
        self,
        *,
        correlation_key: str = DEFAULT_CORRELATION_KEY,
        trace_key: str = DEFAULT_TRACE_KEY,
        context_key: str = DEFAULT_CONTEXT_KEY,
    ) -> None:
        self._correlation_key = correlation_key
        self._trace_key = trace_key
        self._context_key = context_key

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        correlation_id = str(
            context.get(self._correlation_key)
            or uuid.uuid4().hex
        )

        trace_id = str(
            context.get(self._trace_key)
            or uuid.uuid4().hex
        )

        execution_context = ExecutionContext(
            correlation_id=correlation_id,
            trace_id=trace_id,
            started_at=time.time(),
            deadline=None,
        )

        context[self._correlation_key] = correlation_id
        context[self._trace_key] = trace_id
        context[self._context_key] = execution_context

        return await call_next()


# ============================================================
# TIMEOUT MIDDLEWARE
# ============================================================


class TimeoutMiddleware(BaseSystemMiddleware):
    """
    Cancellation-safe timeout enforcement.

    Prevents runaway execution.
    """

    def __init__(
        self,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._timeout = timeout_seconds

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        try:
            return await asyncio.wait_for(
                call_next(),
                timeout=self._timeout,
            )

        except asyncio.TimeoutError as exc:
            raise RequestTimeoutError(
                f"Execution exceeded {self._timeout}s"
            ) from exc


# ============================================================
# REQUEST TIMING MIDDLEWARE
# ============================================================


class TimingMiddleware(BaseSystemMiddleware):
    """
    Ultra-light execution timing middleware.

    Timing capture only.
    Metric aggregation belongs to observability/.
    """

    def __init__(
        self,
        *,
        metrics_key: str = "execution_metrics",
    ) -> None:
        self._metrics_key = metrics_key

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        started = time.perf_counter()
        wall_started = time.time()

        success = False

        try:
            result = await call_next()
            success = True
            return result

        finally:
            completed = time.perf_counter()

            metrics = ExecutionMetrics(
                started_at=wall_started,
                completed_at=time.time(),
                duration_ms=(completed - started) * 1000,
                success=success,
            )

            context[self._metrics_key] = metrics


# ============================================================
# LOGGING HOOK MIDDLEWARE
# ============================================================


class LoggingHookMiddleware(BaseSystemMiddleware):
    """
    Non-blocking logging interception hook.

    Does NOT own log formatting or transport.
    logger/ owns logging infrastructure.
    """

    def __init__(
        self,
        logger: Optional[LoggerManager] = None,
    ) -> None:
        self._logger = logger

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        if self._logger:
            with contextlib.suppress(Exception):
                await self._logger.emit(
                    {
                        "event": "request_started",
                        "correlation_id": context.get(
                            DEFAULT_CORRELATION_KEY
                        ),
                    }
                )

        try:
            return await call_next()

        finally:
            if self._logger:
                with contextlib.suppress(Exception):
                    await self._logger.emit(
                        {
                            "event": "request_completed",
                            "correlation_id": context.get(
                                DEFAULT_CORRELATION_KEY
                            ),
                        }
                    )


# ============================================================
# OBSERVABILITY HOOK MIDDLEWARE
# ============================================================


class ObservabilityMiddleware(BaseSystemMiddleware):
    """
    Lightweight observability bridge.

    Emits execution events without owning telemetry systems.
    """

    def __init__(
        self,
        hook: Optional[ObservabilityHook] = None,
    ) -> None:
        self._hook = hook

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        if self._hook:
            with contextlib.suppress(Exception):
                await self._hook.emit(
                    {
                        "event": "execution_started",
                        "correlation_id": context.get(
                            DEFAULT_CORRELATION_KEY
                        ),
                    }
                )

        try:
            return await call_next()

        finally:
            if self._hook:
                with contextlib.suppress(Exception):
                    await self._hook.emit(
                        {
                            "event": "execution_completed",
                            "correlation_id": context.get(
                                DEFAULT_CORRELATION_KEY
                            ),
                        }
                    )


# ============================================================
# RETRY WRAPPER MIDDLEWARE
# ============================================================


class RetryMiddleware(BaseSystemMiddleware):
    """
    Lightweight retry wrapper.

    Retry policy ownership belongs to resilience/.
    This middleware only performs interception wrapping.
    """

    def __init__(
        self,
        *,
        retries: int = 3,
        retry_delay: float = 0.1,
    ) -> None:
        self._retries = retries
        self._retry_delay = retry_delay

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        last_error: Optional[Exception] = None

        for attempt in range(self._retries + 1):
            try:
                return await call_next()

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                last_error = exc

                if attempt >= self._retries:
                    break

                await asyncio.sleep(self._retry_delay)

        if last_error:
            raise last_error


# ============================================================
# BACKPRESSURE GUARD
# ============================================================


class BackpressureMiddleware(BaseSystemMiddleware):
    """
    Queue/backpressure guard middleware.

    Protects execution pipelines during overload.

    Does NOT own queue infrastructure.
    """

    def __init__(
        self,
        queue_size_provider: Callable[[], int],
        *,
        threshold: int = DEFAULT_LOAD_THRESHOLD,
    ) -> None:
        self._queue_size_provider = queue_size_provider
        self._threshold = threshold

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        size = self._queue_size_provider()

        if size >= self._threshold:
            raise BackpressureExceededError(
                "Execution pipeline overloaded"
            )

        return await call_next()


# ============================================================
# LOAD SHEDDING MIDDLEWARE
# ============================================================


class LoadSheddingMiddleware(BaseSystemMiddleware):
    """
    Lightweight overload rejection middleware.

    Designed for graceful degradation.
    """

    def __init__(
        self,
        load_provider: Callable[[], float],
        *,
        max_load: float,
    ) -> None:
        self._load_provider = load_provider
        self._max_load = max_load

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        current_load = self._load_provider()

        if current_load >= self._max_load:
            raise LoadSheddingError(
                "System overloaded; request shed"
            )

        return await call_next()


# ============================================================
# TRANSACTION GUARD MIDDLEWARE
# ============================================================


class TransactionGuardMiddleware(BaseSystemMiddleware):
    """
    Async transactional boundary wrapper.

    Does NOT own database logic.
    Does NOT own transaction engines.

    Compatible with database adapters.
    """

    def __init__(
        self,
        begin: Callable[[], Awaitable[Any]],
        commit: Callable[[], Awaitable[Any]],
        rollback: Callable[[], Awaitable[Any]],
    ) -> None:
        self._begin = begin
        self._commit = commit
        self._rollback = rollback

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        await self._begin()

        try:
            result = await call_next()
            await self._commit()
            return result

        except Exception:
            with contextlib.suppress(Exception):
                await self._rollback()
            raise


# ============================================================
# SYSTEM CHAIN
# ============================================================


class SystemMiddlewareChain:
    """
    Lightweight infrastructure middleware composition.

    NOT a runtime engine.
    middleware/base.py owns orchestration.
    """

    def __init__(
        self,
        middlewares: Sequence[BaseSystemMiddleware],
    ) -> None:
        self._middlewares = tuple(middlewares)

    async def execute(
        self,
        context: MutableMapping[str, Any],
        endpoint: Callable[[], Awaitable[Any]],
    ) -> Any:
        async def invoke(index: int) -> Any:
            if index >= len(self._middlewares):
                return await endpoint()

            middleware = self._middlewares[index]

            return await middleware.intercept(
                context,
                lambda: invoke(index + 1),
            )

        return await invoke(0)


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "BackpressureExceededError",
    "BackpressureMiddleware",
    "BaseSystemMiddleware",
    "CorrelationMiddleware",
    "ExecutionContext",
    "ExecutionMetrics",
    "LoadSheddingError",
    "LoadSheddingMiddleware",
    "LoggingHookMiddleware",
    "ObservabilityHook",
    "ObservabilityMiddleware",
    "RequestTimeoutError",
    "RetryMiddleware",
    "SystemMiddlewareChain",
    "SystemMiddlewareError",
    "TimeoutMiddleware",
    "TimingMiddleware",
    "TransactionGuardMiddleware",
]
