# ============================================================
# core/runtime/state.py
# ============================================================
# Institutional Runtime State Engine
# ============================================================
#
# RESPONSIBILITIES
# ------------------------------------------------------------
# - centralized runtime state management
# - async-safe state updates
# - health graph tracking
# - runtime observability foundation
# - worker + broker state tracking
# - fault/degradation awareness
# - runtime snapshots
# - distributed-system readiness
#
# DESIGN GOALS
# ------------------------------------------------------------
# - async-first
# - lock-safe
# - immutable snapshots
# - broker compatible
# - event-driven
# - scalable
# - plugin-ready
# - low-overhead
# - microservice-ready later
# - future orchestration compatible
#
# IMPORTANT
# ------------------------------------------------------------
# This file MUST NOT:
# - contain business logic
# - know application domains
# - directly depend on FastAPI
# - directly depend on Redis/Kafka
#
# This file IS:
# - infrastructure intelligence
# - runtime memory
# - operational truth layer
#
# ============================================================

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


# ============================================================
# RUNTIME STATUS
# ============================================================

class RuntimeStatus(str, Enum):
    """
    Global runtime lifecycle states.
    """

    IDLE = "idle"

    BOOTING = "booting"

    RUNNING = "running"

    DEGRADED = "degraded"

    RECOVERING = "recovering"

    SHUTTING_DOWN = "shutting_down"

    STOPPED = "stopped"

    FAILED = "failed"


# ============================================================
# COMPONENT HEALTH
# ============================================================

class ComponentHealth(str, Enum):
    """
    Health classification.

    Important later for:
    - Kubernetes readiness/liveness
    - autoscaling
    - orchestrators
    - recovery systems
    """

    HEALTHY = "healthy"

    DEGRADED = "degraded"

    FAILED = "failed"

    UNKNOWN = "unknown"


# ============================================================
# COMPONENT STATE
# ============================================================

@dataclass(slots=True)
class RuntimeComponent:
    """
    Runtime component state.

    Examples:
    - redis
    - postgres
    - event_bus
    - websocket
    - analytics_worker
    - plugin_loader
    """

    name: str

    health: ComponentHealth = ComponentHealth.UNKNOWN

    status: str = "unknown"

    last_updated: float = field(
        default_factory=time.time
    )

    failures: int = 0

    warnings: int = 0

    metadata: Dict[str, Any] = field(
        default_factory=dict
    )

    latency_ms: Optional[float] = None

    throughput: Optional[float] = None

    def snapshot(self) -> Dict[str, Any]:

        return {
            "name": self.name,
            "health": self.health.value,
            "status": self.status,
            "last_updated": self.last_updated,
            "failures": self.failures,
            "warnings": self.warnings,
            "latency_ms": self.latency_ms,
            "throughput": self.throughput,
            "metadata": deepcopy(self.metadata),
        }


# ============================================================
# RUNTIME STATE ENGINE
# ============================================================

class RuntimeState:
    """
    Institutional-grade runtime state engine.

    This is the operational memory of the platform.

    Responsibilities:
    --------------------------------------------------------
    - runtime lifecycle tracking
    - dependency-aware health graph
    - event throughput tracking
    - infrastructure state tracking
    - worker health tracking
    - broker state tracking
    - observability support
    - orchestration compatibility

    IMPORTANT:
    --------------------------------------------------------
    This class is async-safe.
    ALL mutations go through internal locks.

    WHY:
    --------------------------------------------------------
    Your system is becoming:
    - async-first
    - event-driven
    - concurrent
    - worker-heavy

    Thread locks alone are NOT enough anymore.
    """

    def __init__(self) -> None:

        # ====================================================
        # ASYNC LOCK
        # ====================================================

        self._lock = asyncio.Lock()

        # ====================================================
        # GLOBAL STATE
        # ====================================================

        self._status = RuntimeStatus.IDLE

        self._started_at: Optional[float] = None

        self._boot_duration: Optional[float] = None

        # ====================================================
        # HEALTH GRAPH
        # ====================================================

        self._components: Dict[
            str,
            RuntimeComponent,
        ] = {}

        # ====================================================
        # EVENT METRICS
        # ====================================================

        self._metrics: Dict[str, Any] = {
            "events_processed": 0,
            "events_failed": 0,
            "handlers_executed": 0,
            "handler_failures": 0,
            "retries": 0,
            "broker_failures": 0,
        }

        # ====================================================
        # FAILURE TRACKING
        # ====================================================

        self._failures: Dict[str, int] = {}

        # ====================================================
        # WARNINGS
        # ====================================================

        self._warnings: Dict[str, int] = {}

        # ====================================================
        # DEGRADATION FLAGS
        # ====================================================

        self._degraded_services: set[str] = set()

    # ========================================================
    # LIFECYCLE
    # ========================================================

    async def set_status(
        self,
        status: RuntimeStatus,
    ) -> None:

        async with self._lock:

            self._status = status

            if status == RuntimeStatus.BOOTING:
                self._started_at = time.time()

            elif (
                status == RuntimeStatus.RUNNING
                and self._started_at
            ):
                self._boot_duration = (
                    time.time() - self._started_at
                )

    async def status(self) -> RuntimeStatus:

        async with self._lock:
            return self._status

    # ========================================================
    # COMPONENT REGISTRATION
    # ========================================================

    async def register_component(
        self,
        name: str,
    ) -> None:
        """
        Register runtime component.

        Examples:
        - event_bus
        - redis
        - postgres
        - reward_worker
        """

        async with self._lock:

            if name in self._components:
                return

            self._components[name] = RuntimeComponent(
                name=name
            )

    # ========================================================
    # COMPONENT HEALTH
    # ========================================================

    async def update_component(
        self,
        name: str,
        *,
        health: Optional[
            ComponentHealth
        ] = None,
        status: Optional[str] = None,
        latency_ms: Optional[float] = None,
        throughput: Optional[float] = None,
        metadata: Optional[
            Dict[str, Any]
        ] = None,
    ) -> None:

        async with self._lock:

            component = self._components.get(name)

            if component is None:

                component = RuntimeComponent(
                    name=name
                )

                self._components[name] = component

            if health is not None:
                component.health = health

            if status is not None:
                component.status = status

            if latency_ms is not None:
                component.latency_ms = latency_ms

            if throughput is not None:
                component.throughput = throughput

            if metadata:
                component.metadata.update(metadata)

            component.last_updated = time.time()

    # ========================================================
    # FAILURE TRACKING
    # ========================================================

    async def record_failure(
        self,
        component: str,
    ) -> None:

        async with self._lock:

            self._failures[component] = (
                self._failures.get(component, 0)
                + 1
            )

            if component in self._components:

                self._components[
                    component
                ].failures += 1

                self._components[
                    component
                ].health = (
                    ComponentHealth.DEGRADED
                )

            self._degraded_services.add(
                component
            )

            if (
                self._status
                == RuntimeStatus.RUNNING
            ):
                self._status = (
                    RuntimeStatus.DEGRADED
                )

    # ========================================================
    # WARNING TRACKING
    # ========================================================

    async def record_warning(
        self,
        component: str,
    ) -> None:

        async with self._lock:

            self._warnings[component] = (
                self._warnings.get(component, 0)
                + 1
            )

            if component in self._components:
                self._components[
                    component
                ].warnings += 1

    # ========================================================
    # METRICS
    # ========================================================

    async def increment_metric(
        self,
        metric: str,
        value: int = 1,
    ) -> None:

        async with self._lock:

            self._metrics[metric] = (
                self._metrics.get(metric, 0)
                + value
            )

    async def metric(
        self,
        metric: str,
    ) -> Any:

        async with self._lock:
            return self._metrics.get(metric)

    # ========================================================
    # HEALTH GRAPH
    # ========================================================

    async def health_graph(self) -> Dict[str, Any]:
        """
        Dependency-aware runtime graph.

        Future compatible with:
        - Kubernetes
        - Grafana
        - Prometheus
        - OpenTelemetry
        """

        async with self._lock:

            return {
                "runtime": {
                    "status": self._status.value,
                    "boot_duration": (
                        self._boot_duration
                    ),
                },
                "components": {
                    name: component.snapshot()
                    for (
                        name,
                        component,
                    ) in self._components.items()
                },
                "degraded": list(
                    self._degraded_services
                ),
            }

    # ========================================================
    # SNAPSHOT
    # ========================================================

    async def snapshot(self) -> Dict[str, Any]:
        """
        Immutable runtime snapshot.

        Useful for:
        - debugging
        - admin dashboards
        - monitoring
        - distributed tracing
        """

        async with self._lock:

            return {
                "status": self._status.value,
                "started_at": self._started_at,
                "boot_duration": self._boot_duration,
                "metrics": deepcopy(
                    self._metrics
                ),
                "failures": deepcopy(
                    self._failures
                ),
                "warnings": deepcopy(
                    self._warnings
                ),
                "components": {
                    name: component.snapshot()
                    for (
                        name,
                        component,
                    ) in self._components.items()
                },
                "degraded_services": list(
                    self._degraded_services
                ),
            }

    # ========================================================
    # RESET DEGRADATION
    # ========================================================

    async def recover_component(
        self,
        name: str,
    ) -> None:
        """
        Recovery support for supervisors.

        Future compatible with:
        - circuit breakers
        - auto healing
        - broker recovery
        """

        async with self._lock:

            self._degraded_services.discard(
                name
            )

            component = self._components.get(
                name
            )

            if component:

                component.health = (
                    ComponentHealth.HEALTHY
                )

                component.status = "running"

                component.last_updated = (
                    time.time()
                )

            if (
                not self._degraded_services
                and self._status
                == RuntimeStatus.DEGRADED
            ):
                self._status = (
                    RuntimeStatus.RUNNING
                )


# ============================================================
# GLOBAL STATE SINGLETON
# ============================================================

_runtime_state: Optional[
    RuntimeState
] = None

_runtime_state_lock = asyncio.Lock()


async def get_runtime_state() -> RuntimeState:
    """
    Async-safe singleton accessor.

    Future-ready for:
    - distributed coordinators
    - shared runtime state
    - clustered runtimes
    """

    global _runtime_state

    if _runtime_state is None:

        async with _runtime_state_lock:

            if _runtime_state is None:
                _runtime_state = RuntimeState()

    return _runtime_state
