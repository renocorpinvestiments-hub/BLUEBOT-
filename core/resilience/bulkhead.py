# ============================================================
# core/resilience/bulkhead.py
# ============================================================
# Institutional Resource Isolation Coordination System
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# High-performance workload isolation primitives.
#
# THIS FILE DOES:
# - isolate execution capacity
# - enforce concurrency boundaries
# - prevent resource starvation
# - protect against queue explosions
# - provide async-safe execution isolation
# - support sync + async workloads
# - expose immutable operational snapshots
#
# THIS FILE DOES NOT:
# - supervise workers
# - restart services
# - own runtime orchestration
# - manage queues globally
# - dispatch events
# - own telemetry
# - import runtime internals
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - async-first
# - lock-light coordination
# - deterministic capacity accounting
# - low allocation overhead
# - cancellation-safe execution
# - scalable isolation groups
# - bounded memory pressure
# - zero runtime coupling
# - distributed-system compatible
#
# ============================================================

from __future__ import annotations

import asyncio
import inspect
import time

from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any
from typing import AsyncIterator
from typing import Awaitable
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
# Bulkhead States
# ============================================================

class BulkheadState(str, Enum):
    """
    Operational bulkhead states.
    """

    HEALTHY = "healthy"
    SATURATED = "saturated"
    CLOSED = "closed"


# ============================================================
# Exceptions
# ============================================================

class BulkheadError(Exception):
    """Base bulkhead exception."""


class BulkheadClosedError(BulkheadError):
    """
    Raised when execution is attempted
    against a closed bulkhead.
    """


class BulkheadSaturatedError(BulkheadError):
    """
    Raised when queue capacity is exceeded.
    """


class BulkheadTimeoutError(BulkheadError):
    """
    Raised when capacity acquisition times out.
    """


# ============================================================
# Immutable Policy
# ============================================================

@dataclass(frozen=True, slots=True)
class BulkheadPolicy:
    """
    Immutable bulkhead isolation policy.

    DESIGN GOALS
    --------------------------------------------------------
    - immutable configuration
    - low memory overhead
    - deterministic limits
    - scalable isolation tuning
    """

    max_concurrency: int = 100
    max_queue_size: int = 1000
    acquire_timeout: float = 30.0
    execution_timeout: Optional[float] = None
    enabled: bool = True

    def __post_init__(self) -> None:

        if self.max_concurrency <= 0:
            raise ValueError(
                "max_concurrency must be positive."
            )

        if self.max_queue_size < 0:
            raise ValueError(
                "max_queue_size cannot be negative."
            )

        if self.acquire_timeout <= 0:
            raise ValueError(
                "acquire_timeout must be positive."
            )

        if (
            self.execution_timeout is not None
            and self.execution_timeout <= 0
        ):
            raise ValueError(
                "execution_timeout must be positive."
            )


# ============================================================
# Immutable Snapshot
# ============================================================

@dataclass(frozen=True, slots=True)
class BulkheadSnapshot:
    """
    Immutable operational snapshot.

    Safe for:
    - diagnostics
    - observability
    - orchestration
    - debugging
    """

    name: str
    state: BulkheadState
    max_concurrency: int
    available_permits: int
    active_executions: int
    queued_requests: int
    total_executions: int
    total_rejections: int
    closed: bool
    timestamp: float


# ============================================================
# Bulkhead Group
# ============================================================

class BulkheadGroup:
    """
    Institutional-grade execution isolation group.

    FEATURES
    --------------------------------------------------------
    - async-safe execution isolation
    - bounded concurrency
    - bounded queue pressure
    - cancellation-safe accounting
    - lock-light reads
    - sync + async compatibility
    - deterministic capacity enforcement

    IMPORTANT
    --------------------------------------------------------
    This class ONLY provides isolation primitives.

    It does NOT:
    - own workloads
    - supervise workers
    - manage orchestration
    - own telemetry
    """

    __slots__ = (
        "_name",
        "_policy",
        "_semaphore",
        "_queued",
        "_active",
        "_closed",
        "_lock",
        "_total_executions",
        "_total_rejections",
        "_snapshot",
    )

    def __init__(
        self,
        name: str,
        policy: BulkheadPolicy,
    ) -> None:

        normalized = name.strip().lower()

        if not normalized:
            raise ValueError(
                "Bulkhead name cannot be empty."
            )

        self._name = normalized
        self._policy = policy

        self._semaphore = asyncio.Semaphore(
            policy.max_concurrency
        )

        self._queued: int = 0
        self._active: int = 0

        self._closed: bool = False

        self._lock = asyncio.Lock()

        self._total_executions: int = 0
        self._total_rejections: int = 0

        self._snapshot = self._build_snapshot()

    # ========================================================
    # Properties
    # ========================================================

    @property
    def name(self) -> str:
        return self._name

    @property
    def policy(self) -> BulkheadPolicy:
        return self._policy

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def active_executions(self) -> int:
        return self._active

    @property
    def queued_requests(self) -> int:
        return self._queued

    @property
    def available_permits(self) -> int:
        """
        Fast lock-free permit visibility.

        NOTE:
        --------------------------------------------------------
        asyncio.Semaphore._value is intentionally used
        for ultra-low-overhead operational visibility.

        This avoids unnecessary synchronization.
        """

        return max(0, self._semaphore._value)

    @property
    def saturated(self) -> bool:
        """
        O(1) saturation check.
        """

        return (
            self._active >= self._policy.max_concurrency
            and self._queued >= self._policy.max_queue_size
        )

    # ========================================================
    # Lifecycle
    # ========================================================

    async def close(self) -> None:
        """
        Close bulkhead execution intake.

        Existing executions continue safely.
        """

        async with self._lock:

            if self._closed:
                return

            self._closed = True

            self._snapshot = self._build_snapshot()

    async def open(self) -> None:
        """
        Re-open execution intake.
        """

        async with self._lock:

            if not self._closed:
                return

            self._closed = False

            self._snapshot = self._build_snapshot()

    # ========================================================
    # Execution
    # ========================================================

    async def execute(
        self,
        operation: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        Execute operation within isolated capacity boundary.

        FEATURES
        --------------------------------------------------------
        - queue pressure protection
        - concurrency isolation
        - timeout protection
        - sync + async compatibility
        - cancellation-safe accounting
        """

        async with self.acquire():

            result = operation(
                *args,
                **kwargs,
            )

            if inspect.isawaitable(result):

                if self._policy.execution_timeout is None:
                    return await result

                return await asyncio.wait_for(
                    result,
                    timeout=self._policy.execution_timeout,
                )

            return result

    # ========================================================
    # Capacity Acquisition
    # ========================================================

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """
        Acquire isolated execution capacity safely.

        FEATURES
        --------------------------------------------------------
        - bounded queue pressure
        - cancellation-safe release
        - deterministic accounting
        - timeout protection
        """

        if not self._policy.enabled:
            yield
            return

        if self._closed:
            self._total_rejections += 1
            self._snapshot = self._build_snapshot()

            raise BulkheadClosedError(
                f"Bulkhead '{self._name}' is closed."
            )

        if self._queued >= self._policy.max_queue_size:
            self._total_rejections += 1
            self._snapshot = self._build_snapshot()

            raise BulkheadSaturatedError(
                f"Bulkhead '{self._name}' queue capacity exceeded."
            )

        self._queued += 1
        self._snapshot = self._build_snapshot()

        acquired = False

        try:

            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=self._policy.acquire_timeout,
                )

                acquired = True

            except asyncio.TimeoutError as exc:

                self._total_rejections += 1
                self._snapshot = self._build_snapshot()

                raise BulkheadTimeoutError(
                    f"Bulkhead '{self._name}' "
                    f"acquire timeout exceeded."
                ) from exc

            self._queued -= 1
            self._active += 1
            self._total_executions += 1

            self._snapshot = self._build_snapshot()

            yield

        finally:

            if acquired:

                self._active -= 1

                self._semaphore.release()

            else:

                self._queued = max(
                    0,
                    self._queued - 1,
                )

            self._snapshot = self._build_snapshot()

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(self) -> BulkheadSnapshot:
        """
        Return immutable operational snapshot.

        FEATURES
        --------------------------------------------------------
        - lock-free
        - allocation-light
        - thread-safe
        """

        return self._snapshot

    # ========================================================
    # Internal Snapshot Builder
    # ========================================================

    def _build_snapshot(
        self,
    ) -> BulkheadSnapshot:
        """
        Build immutable bulkhead snapshot.
        """

        if self._closed:
            state = BulkheadState.CLOSED

        elif self.saturated:
            state = BulkheadState.SATURATED

        else:
            state = BulkheadState.HEALTHY

        return BulkheadSnapshot(
            name=self._name,
            state=state,
            max_concurrency=(
                self._policy.max_concurrency
            ),
            available_permits=(
                self.available_permits
            ),
            active_executions=self._active,
            queued_requests=self._queued,
            total_executions=(
                self._total_executions
            ),
            total_rejections=(
                self._total_rejections
            ),
            closed=self._closed,
            timestamp=time.monotonic(),
        )


# ============================================================
# Registry
# ============================================================

class BulkheadRegistry:
    """
    Lightweight bulkhead registry.

    PURPOSE
    --------------------------------------------------------
    - centralized isolation lookup
    - deterministic registration
    - scalable compartment management

    IMPORTANT
    --------------------------------------------------------
    This registry does NOT:
    - own execution
    - supervise workloads
    - orchestrate runtime behavior
    """

    __slots__ = (
        "_groups",
    )

    def __init__(self) -> None:

        self._groups: Dict[
            str,
            BulkheadGroup,
        ] = {}

    # ========================================================
    # Registration
    # ========================================================

    def register(
        self,
        group: BulkheadGroup,
    ) -> None:
        """
        Register bulkhead group.
        """

        if group.name in self._groups:
            raise ValueError(
                f"Bulkhead '{group.name}' already exists."
            )

        self._groups[group.name] = group

    def unregister(
        self,
        name: str,
    ) -> None:
        """
        Remove bulkhead group.
        """

        normalized = name.strip().lower()

        try:
            del self._groups[normalized]

        except KeyError as exc:
            raise KeyError(
                f"Unknown bulkhead '{normalized}'."
            ) from exc

    # ========================================================
    # Lookup
    # ========================================================

    def get(
        self,
        name: str,
    ) -> BulkheadGroup:
        """
        O(1) bulkhead lookup.
        """

        normalized = name.strip().lower()

        try:
            return self._groups[normalized]

        except KeyError as exc:
            raise KeyError(
                f"Unknown bulkhead '{normalized}'."
            ) from exc

    def exists(
        self,
        name: str,
    ) -> bool:
        """
        Fast existence check.
        """

        return (
            name.strip().lower()
            in self._groups
        )

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(
        self,
    ) -> Mapping[str, BulkheadSnapshot]:
        """
        Immutable registry snapshot.
        """

        return MappingProxyType({
            name: group.snapshot()
            for name, group
            in self._groups.items()
        })


# ============================================================
# Default Registry
# ============================================================

DEFAULT_BULKHEADS = BulkheadRegistry()
