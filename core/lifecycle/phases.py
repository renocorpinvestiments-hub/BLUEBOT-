# ============================================================
# core/lifecycle/phases.py
# ============================================================
# Institutional Lifecycle Phase Coordination System
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# Declarative lifecycle phase modeling.
#
# THIS FILE DOES:
# - define lifecycle stages
# - define immutable phase metadata
# - validate dependency graphs
# - produce deterministic boot ordering
# - expose lifecycle coordination primitives
#
# THIS FILE DOES NOT:
# - start services
# - execute runtime logic
# - manage workers
# - import runtime internals
# - control orchestration
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - immutable structures
# - deterministic ordering
# - idempotent resolution
# - async-safe compatibility
# - low allocation overhead
# - scalable registration model
# - zero runtime coupling
# - future distributed-system compatible
#
# ============================================================

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from graphlib import CycleError
from typing import Dict
from typing import Iterable
from typing import Mapping
from typing import Sequence
from typing import Tuple


# ============================================================
# Lifecycle Stages
# ============================================================

class LifecycleStage(str, Enum):
    """
    Global lifecycle stages.

    These stages define high-level operational phases
    of the infrastructure lifecycle.

    They are intentionally lightweight and immutable.
    """

    INIT = "init"
    BOOTSTRAP = "bootstrap"
    RUNTIME = "runtime"
    DEGRADED = "degraded"
    SHUTDOWN = "shutdown"


# ============================================================
# Phase Definition
# ============================================================

@dataclass(frozen=True, slots=True)
class Phase:
    """
    Immutable lifecycle phase definition.

    This object ONLY describes lifecycle ordering metadata.

    It does not execute anything.
    """

    name: str
    priority: int = 100
    requires: Tuple[str, ...] = field(default_factory=tuple)
    timeout: float = 30.0
    critical: bool = True
    stage: LifecycleStage = LifecycleStage.BOOTSTRAP

    def __post_init__(self) -> None:
        """
        Lightweight validation.

        Avoids expensive validation logic while still
        protecting graph integrity.
        """

        normalized_name = self.name.strip().lower()

        if not normalized_name:
            raise ValueError("Phase name cannot be empty.")

        object.__setattr__(self, "name", normalized_name)

        normalized_requires = tuple(
            dep.strip().lower()
            for dep in self.requires
            if dep.strip()
        )

        object.__setattr__(self, "requires", normalized_requires)

        if self.timeout <= 0:
            raise ValueError(
                f"Phase '{self.name}' timeout must be positive."
            )

        if self.name in self.requires:
            raise ValueError(
                f"Phase '{self.name}' cannot depend on itself."
            )


# ============================================================
# Exceptions
# ============================================================

class PhaseError(Exception):
    """Base lifecycle phase exception."""


class DuplicatePhaseError(PhaseError):
    """Raised when duplicate phases are registered."""


class MissingDependencyError(PhaseError):
    """Raised when dependencies are missing."""


class PhaseCycleError(PhaseError):
    """Raised when circular dependencies exist."""


# ============================================================
# Phase Registry
# ============================================================

class PhaseRegistry:
    """
    Immutable-style lifecycle phase registry.

    DESIGN GOALS
    --------------------------------------------------------
    - deterministic ordering
    - idempotent resolution
    - O(1) phase lookup
    - low memory overhead
    - no runtime coupling
    - future plugin compatibility

    IMPORTANT
    --------------------------------------------------------
    This class does NOT execute lifecycle phases.

    It ONLY:
    - stores metadata
    - validates dependencies
    - resolves ordering
    """

    __slots__ = (
        "_phases",
        "_resolved",
        "_dependency_map",
    )

    def __init__(
        self,
        phases: Iterable[Phase] | None = None,
    ) -> None:
        self._phases: Dict[str, Phase] = {}
        self._resolved: Tuple[Phase, ...] | None = None
        self._dependency_map: Dict[str, Tuple[str, ...]] = {}

        if phases:
            for phase in phases:
                self.register(phase)

    # ========================================================
    # Registration
    # ========================================================

    def register(self, phase: Phase) -> None:
        """
        Register a lifecycle phase.

        Registration is deterministic and validated.

        Duplicate registrations are forbidden.
        """

        if phase.name in self._phases:
            raise DuplicatePhaseError(
                f"Phase '{phase.name}' already registered."
            )

        self._phases[phase.name] = phase

        # invalidate cached resolution
        self._resolved = None

    # ========================================================
    # Lookup
    # ========================================================

    def get(self, name: str) -> Phase:
        """
        O(1) phase lookup.
        """

        normalized = name.strip().lower()

        try:
            return self._phases[normalized]

        except KeyError as exc:
            raise KeyError(
                f"Unknown lifecycle phase '{name}'."
            ) from exc

    def exists(self, name: str) -> bool:
        """
        Fast existence check.
        """

        return name.strip().lower() in self._phases

    # ========================================================
    # Validation
    # ========================================================

    def validate(self) -> None:
        """
        Validate lifecycle dependency graph.

        Detects:
        - missing dependencies
        - circular dependencies
        """

        dependency_map: Dict[str, Tuple[str, ...]] = {}

        for phase in self._phases.values():

            missing = [
                dep
                for dep in phase.requires
                if dep not in self._phases
            ]

            if missing:
                raise MissingDependencyError(
                    f"Phase '{phase.name}' "
                    f"requires missing dependencies: {missing}"
                )

            dependency_map[phase.name] = phase.requires

        self._detect_cycles(dependency_map)

        self._dependency_map = dependency_map

    # ========================================================
    # Resolution
    # ========================================================

    def resolve(self) -> Tuple[Phase, ...]:
        """
        Resolve deterministic lifecycle ordering.

        RESULTS
        --------------------------------------------------------
        - stable ordering
        - idempotent output
        - cached resolution
        - topological dependency ordering
        - priority-aware ordering

        RETURNS
        --------------------------------------------------------
        Tuple[Phase, ...]
        """

        if self._resolved is not None:
            return self._resolved

        self.validate()

        # adjacency graph
        graph: Dict[str, set[str]] = defaultdict(set)

        # inbound dependency count
        inbound: Dict[str, int] = defaultdict(int)

        for phase in self._phases.values():
            inbound.setdefault(phase.name, 0)

        for phase in self._phases.values():

            for dep in phase.requires:
                graph[dep].add(phase.name)
                inbound[phase.name] += 1

        # deterministic queue
        queue = deque(
            sorted(
                (
                    name
                    for name, count in inbound.items()
                    if count == 0
                ),
                key=self._sort_key,
            )
        )

        ordered: list[str] = []

        while queue:

            current = queue.popleft()

            ordered.append(current)

            children = sorted(
                graph[current],
                key=self._sort_key,
            )

            for child in children:

                inbound[child] -= 1

                if inbound[child] == 0:
                    queue.append(child)

        if len(ordered) != len(self._phases):
            raise PhaseCycleError(
                "Lifecycle dependency cycle detected."
            )

        resolved = tuple(
            self._phases[name]
            for name in ordered
        )

        # cache immutable result
        self._resolved = resolved

        return resolved

    # ========================================================
    # Utilities
    # ========================================================

    def names(self) -> Tuple[str, ...]:
        """
        Return registered phase names.
        """

        return tuple(sorted(self._phases.keys()))

    def snapshot(self) -> Mapping[str, Phase]:
        """
        Immutable phase snapshot.

        Useful for:
        - diagnostics
        - observability
        - tooling
        - debugging
        """

        return dict(self._phases)

    def clear(self) -> None:
        """
        Clear registry state.

        Useful for:
        - tests
        - isolated environments
        - hot reload systems

        NOTE:
        This does NOT affect external systems.
        """

        self._phases.clear()
        self._dependency_map.clear()
        self._resolved = None

    # ========================================================
    # Internal Helpers
    # ========================================================

    def _sort_key(self, phase_name: str) -> tuple[int, str]:
        """
        Deterministic phase ordering key.

        ORDER:
        --------------------------------------------------------
        1. priority
        2. lexical name

        This guarantees stable ordering across environments.
        """

        phase = self._phases[phase_name]

        return (
            phase.priority,
            phase.name,
        )

    @staticmethod
    def _detect_cycles(
        dependency_map: Mapping[str, Sequence[str]],
    ) -> None:
        """
        Detect circular dependencies.

        Lightweight DFS cycle detection.
        """

        temporary: set[str] = set()
        permanent: set[str] = set()

        def visit(node: str) -> None:

            if node in permanent:
                return

            if node in temporary:
                raise PhaseCycleError(
                    f"Circular lifecycle dependency detected at '{node}'."
                )

            temporary.add(node)

            for dep in dependency_map.get(node, ()):
                visit(dep)

            temporary.remove(node)
            permanent.add(node)

        for node in dependency_map:
            visit(node)


# ============================================================
# Default System Lifecycle
# ============================================================

DEFAULT_PHASES: Tuple[Phase, ...] = (
    Phase(
        name="contracts",
        priority=10,
        stage=LifecycleStage.INIT,
    ),
    Phase(
        name="kernel",
        priority=20,
        requires=("contracts",),
        stage=LifecycleStage.BOOTSTRAP,
    ),
    Phase(
        name="runtime",
        priority=30,
        requires=("kernel",),
        stage=LifecycleStage.RUNTIME,
    ),
    Phase(
        name="queue",
        priority=40,
        requires=("runtime",),
        stage=LifecycleStage.RUNTIME,
    ),
    Phase(
        name="workers",
        priority=50,
        requires=("queue",),
        stage=LifecycleStage.RUNTIME,
    ),
)


# ============================================================
# Factory
# ============================================================

def create_default_registry() -> PhaseRegistry:
    """
    Create a registry with default lifecycle phases.

    This function is intentionally lightweight and
    side-effect free.
    """

    return PhaseRegistry(DEFAULT_PHASES)
