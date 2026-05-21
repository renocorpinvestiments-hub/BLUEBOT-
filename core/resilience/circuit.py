# ============================================================
# core/resilience/circuit.py
# ============================================================
# Institutional Circuit Breaker Protection System
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# High-performance dependency failure isolation layer.
#
# THIS FILE DOES:
# - provide circuit breaker protection primitives
# - isolate unstable dependencies
# - coordinate recovery windows
# - prevent cascading failures
# - expose immutable circuit snapshots
# - support async + sync execution
#
# THIS FILE DOES NOT:
# - restart services
# - supervise workers
# - own telemetry
# - manage orchestration
# - execute runtime scheduling
# - import runtime internals
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - async-first
# - lock-light architecture
# - deterministic state transitions
# - immutable policy configuration
# - cancellation-safe execution
# - low allocation hot paths
# - distributed-system ready
# - zero runtime coupling
#
# ============================================================

from __future__ import annotations

import asyncio
import inspect
import time

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import Mapping
from typing import Optional
from typing import Tuple
from typing import Type


# ============================================================
# Circuit States
# ============================================================

class CircuitState(str, Enum):
    """
    Immutable circuit breaker states.

    CLOSED:
        Normal operation.

    OPEN:
        Requests blocked due to failures.

    HALF_OPEN:
        Recovery probe state.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# ============================================================
# Exceptions
# ============================================================

class CircuitError(Exception):
    """Base circuit breaker exception."""


class CircuitOpenError(CircuitError):
    """
    Raised when execution is blocked
    by an open circuit.
    """


class HalfOpenLimitError(CircuitError):
    """
    Raised when half-open probe capacity
    is exhausted.
    """


# ============================================================
# Immutable Policy
# ============================================================

@dataclass(frozen=True, slots=True)
class CircuitPolicy:
    """
    Immutable circuit breaker configuration.

    DESIGN BENEFITS
    --------------------------------------------------------
    - frozen=True -> idempotent config
    - slots=True -> lower memory usage
    - tuple -> immutable/hash-safe
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_limit: int = 1

    success_threshold: int = 1

    handled_exceptions: Tuple[
        Type[Exception],
        ...
    ] = (Exception,)

    timeout: Optional[float] = None

    def __post_init__(self) -> None:

        if self.failure_threshold <= 0:
            raise ValueError(
                "failure_threshold must be positive."
            )

        if self.recovery_timeout <= 0:
            raise ValueError(
                "recovery_timeout must be positive."
            )

        if self.half_open_limit <= 0:
            raise ValueError(
                "half_open_limit must be positive."
            )

        if self.success_threshold <= 0:
            raise ValueError(
                "success_threshold must be positive."
            )


# ============================================================
# Immutable Snapshot
# ============================================================

@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    """
    Immutable circuit state snapshot.

    Safe for:
    - observability
    - diagnostics
    - orchestration
    - debugging
    """

    name: str
    state: CircuitState
    failures: int
    successes: int
    opened_at: Optional[float]
    half_open_active: int
    timestamp: float


# ============================================================
# Circuit Breaker
# ============================================================

class CircuitBreaker:
    """
    Institutional-grade circuit breaker.

    DESIGN GOALS
    --------------------------------------------------------
    - deterministic state transitions
    - lock-light reads
    - async-safe state mutation
    - half-open recovery protection
    - failure isolation
    - cancellation-safe execution

    IMPORTANT
    --------------------------------------------------------
    This class ONLY provides:
    - dependency protection
    - failure containment

    It does NOT:
    - restart dependencies
    - supervise systems
    - own monitoring
    """

    __slots__ = (
        "_name",
        "_policy",
        "_state",
        "_failures",
        "_successes",
        "_opened_at",
        "_half_open_active",
        "_lock",
        "_snapshot",
    )

    def __init__(
        self,
        name: str,
        policy: CircuitPolicy,
    ) -> None:

        normalized = name.strip().lower()

        if not normalized:
            raise ValueError(
                "Circuit name cannot be empty."
            )

        self._name = normalized
        self._policy = policy

        self._state = CircuitState.CLOSED

        self._failures = 0
        self._successes = 0

        self._opened_at: Optional[float] = None

        self._half_open_active = 0

        self._lock = asyncio.Lock()

        self._snapshot = self._build_snapshot()

    # ========================================================
    # Properties
    # ========================================================

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitState:
        """
        Lock-free state access.
        """

        return self._state

    @property
    def policy(self) -> CircuitPolicy:
        return self._policy

    # ========================================================
    # Public Execution API
    # ========================================================

    async def execute(
        self,
        operation: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute operation under circuit protection.

        FEATURES
        --------------------------------------------------------
        - async-safe
        - sync + async compatible
        - cancellation propagation
        - half-open recovery
        - deterministic protection
        """

        await self._before_execution()

        try:

            result = operation(*args, **kwargs)

            if inspect.isawaitable(result):

                if self._policy.timeout:

                    result = await asyncio.wait_for(
                        result,
                        timeout=self._policy.timeout,
                    )

                else:
                    result = await result

            await self._record_success()

            return result

        except (
            asyncio.CancelledError,
            KeyboardInterrupt,
            SystemExit,
        ):
            raise

        except self._policy.handled_exceptions:
            await self._record_failure()
            raise

    # ========================================================
    # State Coordination
    # ========================================================

    async def _before_execution(self) -> None:
        """
        Validate circuit state before execution.

        FEATURES
        --------------------------------------------------------
        - lock-light reads
        - cooldown handling
        - half-open admission control
        """

        state = self._state

        if state == CircuitState.CLOSED:
            return

        now = time.monotonic()

        async with self._lock:

            state = self._state

            if state == CircuitState.OPEN:

                assert self._opened_at is not None

                elapsed = now - self._opened_at

                if (
                    elapsed
                    >= self._policy.recovery_timeout
                ):
                    self._transition_half_open()

                else:
                    raise CircuitOpenError(
                        f"Circuit '{self._name}' is open."
                    )

            if self._state == CircuitState.HALF_OPEN:

                if (
                    self._half_open_active
                    >= self._policy.half_open_limit
                ):
                    raise HalfOpenLimitError(
                        f"Circuit '{self._name}' "
                        f"half-open capacity exhausted."
                    )

                self._half_open_active += 1

                self._snapshot = (
                    self._build_snapshot()
                )

    async def _record_success(self) -> None:
        """
        Record successful execution.

        FEATURES
        --------------------------------------------------------
        - deterministic recovery
        - half-open stabilization
        """

        async with self._lock:

            if self._state == CircuitState.CLOSED:

                self._failures = 0

            elif self._state == CircuitState.HALF_OPEN:

                self._successes += 1

                self._half_open_active = max(
                    0,
                    self._half_open_active - 1,
                )

                if (
                    self._successes
                    >= self._policy.success_threshold
                ):
                    self._close()

            self._snapshot = self._build_snapshot()

    async def _record_failure(self) -> None:
        """
        Record failed execution.

        FEATURES
        --------------------------------------------------------
        - deterministic failure tracking
        - cascading failure protection
        """

        async with self._lock:

            self._failures += 1

            if self._state == CircuitState.HALF_OPEN:

                self._half_open_active = max(
                    0,
                    self._half_open_active - 1,
                )

                self._open()

            elif self._state == CircuitState.CLOSED:

                if (
                    self._failures
                    >= self._policy.failure_threshold
                ):
                    self._open()

            self._snapshot = self._build_snapshot()

    # ========================================================
    # State Transitions
    # ========================================================

    def _open(self) -> None:
        """
        Transition circuit to OPEN.
        """

        self._state = CircuitState.OPEN

        self._opened_at = time.monotonic()

        self._successes = 0

    def _close(self) -> None:
        """
        Transition circuit to CLOSED.
        """

        self._state = CircuitState.CLOSED

        self._failures = 0
        self._successes = 0

        self._opened_at = None

        self._half_open_active = 0

    def _transition_half_open(self) -> None:
        """
        Transition OPEN -> HALF_OPEN.
        """

        self._state = CircuitState.HALF_OPEN

        self._successes = 0

        self._half_open_active = 0

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(self) -> CircuitSnapshot:
        """
        Return immutable circuit snapshot.

        FEATURES
        --------------------------------------------------------
        - lock-free reads
        - allocation-light
        - thread-safe
        """

        return self._snapshot

    # ========================================================
    # Internal Snapshot Builder
    # ========================================================

    def _build_snapshot(
        self,
    ) -> CircuitSnapshot:
        """
        Build immutable snapshot.
        """

        return CircuitSnapshot(
            name=self._name,
            state=self._state,
            failures=self._failures,
            successes=self._successes,
            opened_at=self._opened_at,
            half_open_active=self._half_open_active,
            timestamp=time.monotonic(),
        )


# ============================================================
# Circuit Registry
# ============================================================

class CircuitRegistry:
    """
    Lightweight circuit registry.

    PURPOSE
    --------------------------------------------------------
    Centralized circuit lookup WITHOUT:
    - orchestration ownership
    - runtime coupling
    - global execution logic
    """

    __slots__ = (
        "_circuits",
    )

    def __init__(self) -> None:

        self._circuits: Dict[
            str,
            CircuitBreaker,
        ] = {}

    # ========================================================
    # Registration
    # ========================================================

    def register(
        self,
        circuit: CircuitBreaker,
    ) -> None:
        """
        Register circuit breaker.
        """

        name = circuit.name

        if name in self._circuits:
            raise ValueError(
                f"Circuit '{name}' already exists."
            )

        self._circuits[name] = circuit

    # ========================================================
    # Lookup
    # ========================================================

    def get(
        self,
        name: str,
    ) -> CircuitBreaker:
        """
        O(1) circuit lookup.
        """

        normalized = name.strip().lower()

        try:
            return self._circuits[normalized]

        except KeyError as exc:
            raise KeyError(
                f"Unknown circuit '{normalized}'."
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
            in self._circuits
        )

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(
        self,
    ) -> Mapping[str, CircuitSnapshot]:
        """
        Immutable registry snapshot.
        """

        return MappingProxyType({
            name: circuit.snapshot()
            for name, circuit
            in self._circuits.items()
        })


# ============================================================
# Global Default Registry
# ============================================================

DEFAULT_CIRCUITS = CircuitRegistry()
