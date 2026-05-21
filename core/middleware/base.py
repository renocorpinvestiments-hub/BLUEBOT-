"""
RENOCORP CORE MIDDLEWARE PIPELINE
=================================

Institutional-grade middleware execution kernel.

This module is intentionally responsibility-pure.

Responsibilities:
- middleware execution
- async pipeline orchestration
- execution chaining
- immutable context propagation
- cancellation propagation
- timeout propagation
- short-circuit execution
- middleware composition
- deterministic execution order
- lightweight execution wrapping

DOES NOT:
- implement security policies
- implement retry policies
- implement logging systems
- implement observability analytics
- manage runtime lifecycle
- own queue systems
- own plugin systems
- implement business logic
- manage persistence

Architecture Boundaries:
middleware/base.py     = execution mechanics
middleware/security.py = security interception
middleware/system.py   = infrastructure interception
runtime/               = orchestration authority
observability/         = telemetry ownership
security/              = policy ownership
logger/                = transport ownership
queue/                 = delivery ownership

Design Goals:
- async-first
- deterministic execution
- ultra-low overhead
- zero shared mutable state
- cancellation-safe
- timeout-safe
- composable
- scalable
- idempotent
- lock-minimized
- immutable execution contracts
"""

from __future__ import annotations

import abc
import asyncio
import contextvars
import inspect
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Generic,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    TypeVar,
)

# ============================================================
# GENERICS
# ============================================================

T = TypeVar("T")

# ============================================================
# CONTEXT VARS
# ============================================================

_current_context: contextvars.ContextVar[
    Optional["MiddlewareContext"]
] = contextvars.ContextVar(
    "middleware_context",
    default=None,
)

# ============================================================
# CONSTANTS
# ============================================================

DEFAULT_TIMEOUT_SECONDS = 30.0

# ============================================================
# EXCEPTIONS
# ============================================================


class MiddlewareError(RuntimeError):
    """Base middleware error."""


class MiddlewareExecutionError(MiddlewareError):
    """Middleware execution failure."""


class MiddlewareTimeoutError(MiddlewareError):
    """Execution timeout exceeded."""


class MiddlewareCancelledError(MiddlewareError):
    """Execution cancelled."""


class MiddlewareShortCircuit(MiddlewareError):
    """
    Internal short-circuit signal.

    Used for:
    - cached responses
    - early termination
    - guard rejection
    """

    def __init__(self, result: Any):
        self.result = result
        super().__init__("Pipeline short-circuited")


# ============================================================
# IMMUTABLE CONTEXT
# ============================================================


@dataclass(frozen=True, slots=True)
class MiddlewareContext:
    """
    Immutable middleware execution context.

    Concurrency-safe.
    Task-safe.
    """

    request_id: str
    started_at: float
    deadline: Optional[float]
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def with_metadata(
        self,
        **values: Any,
    ) -> "MiddlewareContext":
        merged = dict(self.metadata)
        merged.update(values)

        return MiddlewareContext(
            request_id=self.request_id,
            started_at=self.started_at,
            deadline=self.deadline,
            metadata=MappingProxyType(merged),
        )


# ============================================================
# EXECUTION RESULT
# ============================================================


@dataclass(frozen=True, slots=True)
class MiddlewareResult(Generic[T]):
    """
    Immutable pipeline execution result.
    """

    value: T
    duration_ms: float
    success: bool
    middleware_count: int


# ============================================================
# NEXT CALLABLE
# ============================================================


class NextCallable(Protocol):
    async def __call__(self) -> Any:
        ...


# ============================================================
# BASE MIDDLEWARE
# ============================================================


class BaseMiddleware(abc.ABC):
    """
    Base middleware contract.

    Middleware must:
    - remain stateless where possible
    - avoid blocking I/O
    - avoid shared mutable state
    - avoid orchestration ownership
    """

    __slots__ = ()

    @abc.abstractmethod
    async def process(
        self,
        context: MiddlewareContext,
        call_next: NextCallable,
    ) -> Any:
        """
        Intercept execution pipeline.
        """
        raise NotImplementedError


# ============================================================
# FUNCTION MIDDLEWARE
# ============================================================


class FunctionMiddleware(BaseMiddleware):
    """
    Lightweight functional middleware adapter.
    """

    __slots__ = ("_func",)

    def __init__(
        self,
        func: Callable[
            [MiddlewareContext, NextCallable],
            Awaitable[Any],
        ],
    ) -> None:
        self._func = func

    async def process(
        self,
        context: MiddlewareContext,
        call_next: NextCallable,
    ) -> Any:
        return await self._func(context, call_next)


# ============================================================
# PIPELINE
# ============================================================


class MiddlewarePipeline:
    """
    Immutable middleware pipeline.

    Features:
    - deterministic ordering
    - cancellation propagation
    - timeout propagation
    - short-circuit support
    - idempotent composition
    - lock-free reads
    """

    __slots__ = (
        "_middlewares",
        "_timeout",
    )

    def __init__(
        self,
        middlewares: Sequence[BaseMiddleware] = (),
        *,
        timeout: Optional[float] = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._middlewares = tuple(middlewares)
        self._timeout = timeout

    @property
    def middlewares(self) -> Sequence[BaseMiddleware]:
        return self._middlewares

    def add(
        self,
        middleware: BaseMiddleware,
    ) -> "MiddlewarePipeline":
        """
        Returns NEW pipeline.

        Immutable composition prevents:
        - runtime mutation races
        - accidental overwrite
        - plugin corruption
        """
        return MiddlewarePipeline(
            (
                *self._middlewares,
                middleware,
            ),
            timeout=self._timeout,
        )

    def extend(
        self,
        middlewares: Sequence[BaseMiddleware],
    ) -> "MiddlewarePipeline":
        return MiddlewarePipeline(
            (
                *self._middlewares,
                *middlewares,
            ),
            timeout=self._timeout,
        )

    async def execute(
        self,
        context: MiddlewareContext,
        endpoint: Callable[[], Awaitable[T]],
    ) -> MiddlewareResult[T]:
        """
        Execute middleware chain.
        """

        started = time.perf_counter()

        token = _current_context.set(context)

        try:

            async def invoke(index: int) -> Any:
                if index >= len(self._middlewares):
                    return await endpoint()

                middleware = self._middlewares[index]

                return await middleware.process(
                    context,
                    lambda: invoke(index + 1),
                )

            if self._timeout is not None:
                value = await asyncio.wait_for(
                    invoke(0),
                    timeout=self._timeout,
                )
            else:
                value = await invoke(0)

            duration = (
                time.perf_counter() - started
            ) * 1000

            return MiddlewareResult(
                value=value,
                duration_ms=duration,
                success=True,
                middleware_count=len(
                    self._middlewares
                ),
            )

        except MiddlewareShortCircuit as exc:
            duration = (
                time.perf_counter() - started
            ) * 1000

            return MiddlewareResult(
                value=exc.result,
                duration_ms=duration,
                success=True,
                middleware_count=len(
                    self._middlewares
                ),
            )

        except asyncio.TimeoutError as exc:
            raise MiddlewareTimeoutError(
                f"Pipeline exceeded timeout "
                f"({self._timeout}s)"
            ) from exc

        except asyncio.CancelledError as exc:
            raise MiddlewareCancelledError(
                "Pipeline execution cancelled"
            ) from exc

        except Exception as exc:
            raise MiddlewareExecutionError(
                "Middleware pipeline failed"
            ) from exc

        finally:
            _current_context.reset(token)


# ============================================================
# PIPELINE EXECUTOR
# ============================================================


class PipelineExecutor:
    """
    Idempotent execution facade.

    Safe reusable execution wrapper.
    """

    __slots__ = (
        "_pipeline",
    )

    def __init__(
        self,
        pipeline: MiddlewarePipeline,
    ) -> None:
        self._pipeline = pipeline

    async def execute(
        self,
        context: MiddlewareContext,
        endpoint: Callable[[], Awaitable[T]],
    ) -> MiddlewareResult[T]:
        return await self._pipeline.execute(
            context,
            endpoint,
        )


# ============================================================
# HELPERS
# ============================================================


def create_context(
    *,
    request_id: str,
    metadata: Optional[
        Mapping[str, Any]
    ] = None,
    timeout: Optional[float] = None,
) -> MiddlewareContext:
    """
    Create immutable middleware context.
    """

    now = time.time()

    deadline = (
        now + timeout
        if timeout is not None
        else None
    )

    return MiddlewareContext(
        request_id=request_id,
        started_at=now,
        deadline=deadline,
        metadata=MappingProxyType(
            dict(metadata or {})
        ),
    )


def get_current_context() -> Optional[
    MiddlewareContext
]:
    """
    Returns current task-local context.
    """

    return _current_context.get()


def middleware(
    func: Callable[
        [MiddlewareContext, NextCallable],
        Awaitable[Any],
    ],
) -> FunctionMiddleware:
    """
    Decorator for functional middleware.
    """

    if not inspect.iscoroutinefunction(func):
        raise TypeError(
            "Middleware function must be async"
        )

    return FunctionMiddleware(func)


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "BaseMiddleware",
    "FunctionMiddleware",
    "MiddlewareCancelledError",
    "MiddlewareContext",
    "MiddlewareError",
    "MiddlewareExecutionError",
    "MiddlewarePipeline",
    "MiddlewareResult",
    "MiddlewareShortCircuit",
    "MiddlewareTimeoutError",
    "NextCallable",
    "PipelineExecutor",
    "create_context",
    "get_current_context",
    "middleware",
]
