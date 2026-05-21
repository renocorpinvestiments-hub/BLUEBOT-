# ============================================================
# core/lifecycle/readiness.py
# ============================================================
# Institutional Runtime Readiness Coordination System
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# Global operational readiness authority.
#
# THIS FILE DOES:
# - define operational readiness states
# - coordinate readiness transitions
# - expose immutable readiness snapshots
# - manage capability readiness registration
# - validate state transitions
# - provide async-safe readiness coordination
#
# THIS FILE DOES NOT:
# - monitor telemetry
# - supervise workers
# - execute runtime logic
# - manage orchestration
# - restart services
# - import runtime internals
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - immutable snapshots
# - deterministic transitions
# - idempotent operations
# - lock-light architecture
# - async-safe mutations
# - O(1) readiness checks
# - zero runtime coupling
# - future distributed-system compatible
#
# ============================================================

from __future__ import annotations

import asyncio
import time

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Dict
from typing import Mapping
from typing import Optional
from typing import Tuple


# ============================================================
# Readiness States
# ============================================================

class ReadinessState(str, Enum):
    """
    Global operational readiness states.

    These represent the official operational state
    of the infrastructure runtime.
    """

    BOOTING = "booting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    FAILED = "failed"


# ============================================================
# Valid State Transitions
# ============================================================

_VALID_TRANSITIONS: Mapping[
    ReadinessState,
    Tuple[ReadinessState, ...],
] = MappingProxyType({
    ReadinessState.BOOTING: (
        ReadinessState.READY,
        ReadinessState.DEGRADED,
        ReadinessState.FAILED,
        ReadinessState.STOPPING,
    ),

    ReadinessState.READY: (
        ReadinessState.DEGRADED,
        ReadinessState.STOPPING,
        ReadinessState.FAILED,
    ),

    ReadinessState.DEGRADED: (
        ReadinessState.READY,
        ReadinessState.STOPPING,
        ReadinessState.FAILED,
    ),

    ReadinessState.STOPPING: (
        ReadinessState.FAILED,
    ),

    ReadinessState.FAILED: (),
})


# ============================================================
# Exceptions
# ============================================================

class ReadinessError(Exception):
    """Base readiness exception."""


class InvalidTransitionError(ReadinessError):
    """Raised on invalid readiness transitions."""


class CapabilityExistsError(ReadinessError):
    """Raised on duplicate capability registration."""


class UnknownCapabilityError(ReadinessError):
    """Raised when capability does not exist."""


# ============================================================
# Immutable Snapshot
# ============================================================

@dataclass(frozen=True, slots=True)
class ReadinessSnapshot:
    """
    Immutable operational readiness snapshot.

    Safe for:
    - APIs
    - diagnostics
    - observability
    - orchestration
    - distributed systems
    """

    state: ReadinessState
    capabilities: Mapping[str, bool]
    degraded: bool
    maintenance_mode: bool
    timestamp: float


# ============================================================
# Readiness Manager
# ============================================================

class ReadinessManager:
    """
    Institutional-grade readiness authority.

    DESIGN GOALS
    --------------------------------------------------------
    - lock-light reads
    - async-safe mutations
    - deterministic transitions
    - immutable snapshots
    - scalable capability registration
    - zero runtime coupling

    IMPORTANT
    --------------------------------------------------------
    This class does NOT:
    - supervise systems
    - execute runtime actions
    - monitor telemetry
    - restart services

    It ONLY defines operational readiness truth.
    """

    __slots__ = (
        "_state",
        "_capabilities",
        "_maintenance_mode",
        "_lock",
        "_snapshot",
    )

    def __init__(self) -> None:

        self._state: ReadinessState = (
            ReadinessState.BOOTING
        )

        self._capabilities: Dict[str, bool] = {}

        self._maintenance_mode: bool = False

        self._lock = asyncio.Lock()

        self._snapshot = self._build_snapshot()

    # ========================================================
    # State Access
    # ========================================================

    @property
    def state(self) -> ReadinessState:
        """
        Fast lock-free state access.
        """

        return self._state

    @property
    def degraded(self) -> bool:
        """
        O(1) degraded state check.
        """

        return self._state == ReadinessState.DEGRADED

    @property
    def ready(self) -> bool:
        """
        O(1) readiness check.
        """

        return self._state == ReadinessState.READY

    @property
    def maintenance_mode(self) -> bool:
        """
        O(1) maintenance state check.
        """

        return self._maintenance_mode

    # ========================================================
    # State Transitions
    # ========================================================

    async def transition(
        self,
        target: ReadinessState,
    ) -> None:
        """
        Perform validated readiness transition.

        FEATURES
        --------------------------------------------------------
        - async-safe
        - deterministic
        - idempotent
        - transition validated
        - snapshot updated atomically
        """

        async with self._lock:

            current = self._state

            # idempotent transition
            if current == target:
                return

            allowed = _VALID_TRANSITIONS[current]

            if target not in allowed:
                raise InvalidTransitionError(
                    f"Invalid readiness transition: "
                    f"{current.value} -> {target.value}"
                )

            self._state = target

            self._snapshot = self._build_snapshot()

    # ========================================================
    # Capability Registration
    # ========================================================

    async def register_capability(
        self,
        name: str,
        *,
        ready: bool = False,
    ) -> None:
        """
        Register a readiness capability.

        Examples:
        - queue
        - runtime
        - dispatch
        - plugins
        - workers

        IMPORTANT
        --------------------------------------------------------
        This system NEVER imports those subsystems.

        Capability registration keeps the architecture
        decoupled and scalable.
        """

        normalized = name.strip().lower()

        if not normalized:
            raise ValueError(
                "Capability name cannot be empty."
            )

        async with self._lock:

            if normalized in self._capabilities:
                raise CapabilityExistsError(
                    f"Capability '{normalized}' "
                    f"already registered."
                )

            self._capabilities[normalized] = ready

            self._snapshot = self._build_snapshot()

    async def unregister_capability(
        self,
        name: str,
    ) -> None:
        """
        Remove capability registration.

        Useful for:
        - plugins
        - hot reload
        - worker draining
        - testing
        """

        normalized = name.strip().lower()

        async with self._lock:

            if normalized not in self._capabilities:
                raise UnknownCapabilityError(
                    f"Unknown capability '{normalized}'."
                )

            del self._capabilities[normalized]

            self._snapshot = self._build_snapshot()

    # ========================================================
    # Capability Updates
    # ========================================================

    async def set_capability(
        self,
        name: str,
        ready: bool,
    ) -> None:
        """
        Update capability readiness state.

        FEATURES
        --------------------------------------------------------
        - async-safe
        - idempotent
        - lock-light
        """

        normalized = name.strip().lower()

        async with self._lock:

            if normalized not in self._capabilities:
                raise UnknownCapabilityError(
                    f"Unknown capability '{normalized}'."
                )

            current = self._capabilities[normalized]

            # idempotent update
            if current == ready:
                return

            self._capabilities[normalized] = ready

            self._snapshot = self._build_snapshot()

    # ========================================================
    # Capability Checks
    # ========================================================

    def is_capability_ready(
        self,
        name: str,
    ) -> bool:
        """
        O(1) capability readiness check.

        Lock-free read path for maximum performance.
        """

        return self._capabilities.get(
            name.strip().lower(),
            False,
        )

    def capabilities(self) -> Tuple[str, ...]:
        """
        Return registered capability names.
        """

        return tuple(
            sorted(self._capabilities.keys())
        )

    def all_capabilities_ready(self) -> bool:
        """
        Fast aggregate readiness check.

        Useful for:
        - deployment gates
        - orchestrators
        - readiness probes
        """

        return all(self._capabilities.values())

    # ========================================================
    # Maintenance Mode
    # ========================================================

    async def set_maintenance_mode(
        self,
        enabled: bool,
    ) -> None:
        """
        Toggle maintenance mode.

        Maintenance mode does NOT affect:
        - lifecycle execution
        - runtime orchestration
        - supervision

        It ONLY exposes operational state.
        """

        async with self._lock:

            if self._maintenance_mode == enabled:
                return

            self._maintenance_mode = enabled

            self._snapshot = self._build_snapshot()

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(self) -> ReadinessSnapshot:
        """
        Return immutable readiness snapshot.

        FEATURES
        --------------------------------------------------------
        - lock-free
        - allocation-light
        - thread-safe
        - async-safe
        """

        return self._snapshot

    # ========================================================
    # Internal Snapshot Builder
    # ========================================================

    def _build_snapshot(self) -> ReadinessSnapshot:
        """
        Build immutable readiness snapshot.

        IMPORTANT
        --------------------------------------------------------
        Uses immutable mappings to prevent accidental mutation.
        """

        capabilities = MappingProxyType(
            dict(self._capabilities)
        )

        return ReadinessSnapshot(
            state=self._state,
            capabilities=capabilities,
            degraded=(
                self._state
                == ReadinessState.DEGRADED
            ),
            maintenance_mode=self._maintenance_mode,
            timestamp=time.monotonic(),
        )


# ============================================================
# Global Default Readiness Authority
# ============================================================

DEFAULT_READINESS = ReadinessManager()
