# ============================================================
# core/resilience/retry.py
# ============================================================
# Institutional Retry Coordination System
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# Reusable retry coordination primitives.
#
# THIS FILE DOES:
# - coordinate retry execution
# - provide retry policies
# - support async + sync retries
# - enforce retry budgets
# - provide backoff strategies
# - isolate retry failures
# - support cancellation-safe retries
#
# THIS FILE DOES NOT:
# - supervise workers
# - restart services
# - execute orchestration
# - manage queues
# - own telemetry
# - dispatch runtime events
# - manage runtime execution
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - async-first
# - lock-free execution paths
# - immutable retry policies
# - deterministic retry scheduling
# - cancellation safety
# - timeout isolation
# - scalable retry primitives
# - low allocation overhead
# - zero runtime coupling
# - distributed-system ready
#
# ============================================================

from __future__ import annotations

import asyncio
import inspect
import random
import time

from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional
from typing import Tuple
from typing import Type


# ============================================================
# Constants
# ============================================================

_NON_RETRYABLE_EXCEPTIONS = (
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
)


# ============================================================
# Retry Strategy
# ============================================================

class RetryStrategy(str, Enum):
    """
    Supported retry backoff strategies.
    """

    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


# ============================================================
# Exceptions
# ============================================================

class RetryError(Exception):
    """Base retry exception."""


class RetryExhaustedError(RetryError):
    """
    Raised when retry attempts are exhausted.
    """


class RetryBudgetExceededError(RetryError):
    """
    Raised when retry budget is exceeded.
    """


# ============================================================
# Retry Result
# ============================================================

@dataclass(frozen=True, slots=True)
class RetryResult:
    """
    Immutable retry execution result.

    Safe for:
    - diagnostics
    - observability
    - orchestration
    - tracing
    """

    success: bool
    attempts: int
    duration: float
    final_error: Optional[str]


# ============================================================
# Retry Policy
# ============================================================

@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """
    Immutable retry execution policy.

    FEATURES
    --------------------------------------------------------
    - deterministic configuration
    - low memory overhead
    - scalable retry isolation
    - async-safe execution
    """

    max_attempts: int = 3

    base_delay: float = 0.5

    max_delay: float = 30.0

    strategy: RetryStrategy = (
        RetryStrategy.EXPONENTIAL
    )

    jitter: bool = True

    timeout: Optional[float] = None

    max_total_delay: Optional[float] = None

    retry_exceptions: Tuple[
        Type[Exception],
        ...
    ] = (Exception,)

    def __post_init__(self) -> None:

        if self.max_attempts <= 0:
            raise ValueError(
                "max_attempts must be positive."
            )

        if self.base_delay < 0:
            raise ValueError(
                "base_delay cannot be negative."
            )

        if self.max_delay <= 0:
            raise ValueError(
                "max_delay must be positive."
            )

        if self.timeout is not None:

            if self.timeout <= 0:
                raise ValueError(
                    "timeout must be positive."
                )

        if self.max_total_delay is not None:

            if self.max_total_delay <= 0:
                raise ValueError(
                    "max_total_delay must be positive."
                )

        if not self.retry_exceptions:
            raise ValueError(
                "retry_exceptions cannot be empty."
            )


# ============================================================
# Retry Executor
# ============================================================

class RetryExecutor:
    """
    Institutional-grade retry executor.

    DESIGN GOALS
    --------------------------------------------------------
    - deterministic retries
    - cancellation-safe execution
    - timeout isolation
    - scalable retry coordination
    - lock-free hot paths
    - low allocation overhead

    IMPORTANT
    --------------------------------------------------------
    This executor ONLY coordinates retries.

    It does NOT:
    - supervise systems
    - restart workers
    - manage queues
    - own orchestration
    """

    __slots__ = (
        "_policy",
        "_schedule",
    )

    def __init__(
        self,
        policy: RetryPolicy,
    ) -> None:

        self._policy = policy

        self._schedule = (
            self._build_schedule(policy)
        )

    # ========================================================
    # Public Execution API
    # ========================================================

    async def execute(
        self,
        operation: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> RetryResult:
        """
        Execute operation with retry protection.

        FEATURES
        --------------------------------------------------------
        - async-safe
        - sync + async support
        - cancellation-safe
        - retry budget enforcement
        - timeout isolation
        - deterministic retry scheduling
        """

        started = time.monotonic()

        attempts = 0

        total_delay = 0.0

        last_error: Optional[Exception] = None

        while attempts < self._policy.max_attempts:

            attempts += 1

            try:

                result = operation(
                    *args,
                    **kwargs,
                )

                if inspect.isawaitable(result):

                    if self._policy.timeout:

                        await asyncio.wait_for(
                            result,
                            timeout=self._policy.timeout,
                        )

                    else:
                        await result

                duration = (
                    time.monotonic() - started
                )

                return RetryResult(
                    success=True,
                    attempts=attempts,
                    duration=duration,
                    final_error=None,
                )

            except _NON_RETRYABLE_EXCEPTIONS:
                raise

            except Exception as exc:

                last_error = exc

                if not isinstance(
                    exc,
                    self._policy.retry_exceptions,
                ):
                    raise

                if attempts >= self._policy.max_attempts:
                    break

                delay = self._schedule[
                    attempts - 1
                ]

                total_delay += delay

                if (
                    self._policy.max_total_delay
                    is not None
                    and total_delay
                    > self._policy.max_total_delay
                ):
                    raise RetryBudgetExceededError(
                        "Retry delay budget exceeded."
                    ) from exc

                await asyncio.sleep(delay)

        duration = (
            time.monotonic() - started
        )

        raise RetryExhaustedError(
            f"Retry attempts exhausted after "
            f"{attempts} attempts."
        ) from last_error

    # ========================================================
    # Schedule Access
    # ========================================================

    @property
    def policy(self) -> RetryPolicy:
        """
        Immutable retry policy access.
        """

        return self._policy

    def retry_schedule(self) -> Tuple[float, ...]:
        """
        Return immutable retry schedule.

        Useful for:
        - diagnostics
        - testing
        - observability
        """

        return self._schedule

    # ========================================================
    # Internal Delay Schedule Builder
    # ========================================================

    @staticmethod
    def _build_schedule(
        policy: RetryPolicy,
    ) -> Tuple[float, ...]:
        """
        Precompute retry delay schedule.

        FEATURES
        --------------------------------------------------------
        - deterministic
        - allocation-light
        - low runtime overhead
        - scalable retry coordination
        """

        delays = []

        for attempt in range(
            policy.max_attempts - 1
        ):

            delay = RetryExecutor._compute_delay(
                strategy=policy.strategy,
                base_delay=policy.base_delay,
                max_delay=policy.max_delay,
                attempt=attempt,
            )

            if policy.jitter:
                delay = random.uniform(
                    0,
                    delay,
                )

            delays.append(delay)

        return tuple(delays)

    @staticmethod
    def _compute_delay(
        *,
        strategy: RetryStrategy,
        base_delay: float,
        max_delay: float,
        attempt: int,
    ) -> float:
        """
        Compute retry delay.

        Supported:
        - fixed
        - linear
        - exponential

        IMPORTANT
        --------------------------------------------------------
        Uses monotonic deterministic scheduling logic.
        """

        if strategy == RetryStrategy.FIXED:

            delay = base_delay

        elif strategy == RetryStrategy.LINEAR:

            delay = (
                base_delay * (attempt + 1)
            )

        else:

            delay = (
                base_delay * (2 ** attempt)
            )

        return min(delay, max_delay)


# ============================================================
# Decorator API
# ============================================================

def with_retry(
    policy: RetryPolicy,
) -> Callable[
    [Callable[..., Any]],
    Callable[..., Awaitable[RetryResult]],
]:
    """
    Declarative retry decorator.

    Example
    --------------------------------------------------------

    @with_retry(
        RetryPolicy(max_attempts=5)
    )
    async def fetch():
        ...

    Improves:
    - extension scalability
    - plugin ergonomics
    - modular retry integration
    """

    executor = RetryExecutor(policy)

    def decorator(
        operation: Callable[..., Any],
    ) -> Callable[..., Awaitable[RetryResult]]:

        async def wrapper(
            *args: Any,
            **kwargs: Any,
        ) -> RetryResult:

            return await executor.execute(
                operation,
                *args,
                **kwargs,
            )

        return wrapper

    return decorator


# ============================================================
# Default Retry Policies
# ============================================================

DEFAULT_RETRY_POLICY = RetryPolicy()

AGGRESSIVE_RETRY_POLICY = RetryPolicy(
    max_attempts=6,
    base_delay=0.25,
    max_delay=10.0,
)

CONSERVATIVE_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=2.0,
    max_delay=60.0,
)
