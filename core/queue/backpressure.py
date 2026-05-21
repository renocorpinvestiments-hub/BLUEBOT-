# core/queue/backpressure.py

from __future__ import annotations

import asyncio
import time

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

# ============================================================
# CONSTANTS
# ============================================================

MONOTONIC = time.monotonic

# ============================================================
# PRESSURE LEVEL
# ============================================================


class PressureLevel(str, Enum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    CRITICAL = "critical"


# ============================================================
# SNAPSHOT
# ============================================================


@dataclass(slots=True)
class PressureSnapshot:
    level: PressureLevel
    score: float

    queue_depth: int
    inflight: int
    latency_ms: float
    failure_rate: float

    concurrency_limit: int

    updated_at: float


# ============================================================
# CONTROLLER
# ============================================================


class BackpressureController:
    """
    Institutional-grade adaptive backpressure controller.

    Responsibilities:
    - monitor runtime pressure
    - compute deterministic pressure state
    - expose concurrency hints
    - expose load shedding decisions
    - telemetry emission

    Explicitly NOT responsible for:
    - retries
    - DLQ
    - ACK/NACK
    - broker state
    - message mutation
    - execution orchestration
    """

    def __init__(
        self,
        *,
        telemetry: Optional[Any] = None,
        state_store: Optional[Any] = None,
        monitor_interval: float = 1.0,
        min_concurrency: int = 1,
        max_concurrency: int = 256,
        target_latency_ms: float = 1000.0,
        critical_queue_depth: int = 100_000,
        degraded_queue_depth: int = 25_000,
        hysteresis_seconds: float = 5.0,
    ) -> None:

        self._telemetry = telemetry
        self._state_store = state_store

        self._monitor_interval = max(
            0.1,
            monitor_interval,
        )

        self._min_concurrency = max(
            1,
            min_concurrency,
        )

        self._max_concurrency = max(
            self._min_concurrency,
            max_concurrency,
        )

        self._target_latency_ms = max(
            1.0,
            target_latency_ms,
        )

        self._critical_queue_depth = max(
            1,
            critical_queue_depth,
        )

        self._degraded_queue_depth = max(
            1,
            degraded_queue_depth,
        )

        self._hysteresis_seconds = max(
            1.0,
            hysteresis_seconds,
        )

        self._running = False

        self._lock = asyncio.Lock()

        self._monitor_task: Optional[
            asyncio.Task
        ] = None

        self._metrics_provider: Optional[Any] = None

        now = MONOTONIC()

        self._snapshot = PressureSnapshot(
            level=PressureLevel.NORMAL,
            score=0.0,
            queue_depth=0,
            inflight=0,
            latency_ms=0.0,
            failure_rate=0.0,
            concurrency_limit=max_concurrency,
            updated_at=now,
        )

        self._last_transition = now

    # ============================================================
    # PUBLIC API
    # ============================================================

    def bind_metrics_provider(
        self,
        provider: Any,
    ) -> None:
        """
        Provider must expose:

        async def snapshot() -> Dict[str, Any]
        """
        self._metrics_provider = provider

    def snapshot(
        self,
    ) -> PressureSnapshot:
        return self._snapshot

    def level(
        self,
    ) -> PressureLevel:
        return self._snapshot.level

    def concurrency_limit(
        self,
    ) -> int:
        return self._snapshot.concurrency_limit

    def should_drop(
        self,
        *,
        priority: int = 0,
    ) -> bool:
        """
        Deterministic load shedding.

        Higher priority survives pressure.
        """

        level = self._snapshot.level

        if level == PressureLevel.NORMAL:
            return False

        if level == PressureLevel.DEGRADED:
            return priority < 0

        return priority < 5

    # ============================================================
    # LIFECYCLE
    # ============================================================

    async def start(
        self,
    ) -> None:

        if self._running:
            return

        self._running = True

        self._monitor_task = asyncio.create_task(
            self._monitor_loop(),
            name="backpressure-monitor",
        )

        await self._emit_metric(
            "backpressure.started",
            {},
        )

    async def stop(
        self,
    ) -> None:

        if not self._running:
            return

        self._running = False

        if self._monitor_task:

            self._monitor_task.cancel()

            try:
                await self._monitor_task

            except asyncio.CancelledError:
                pass

        await self._emit_metric(
            "backpressure.stopped",
            {},
        )

    # ============================================================
    # MONITOR LOOP
    # ============================================================

    async def _monitor_loop(
        self,
    ) -> None:

        while self._running:

            try:

                metrics = await self._collect_metrics()

                await self._recompute(metrics)

                await asyncio.sleep(
                    self._monitor_interval
                )

            except asyncio.CancelledError:
                raise

            except Exception as exc:

                await self._emit_error(
                    "backpressure.monitor_failure",
                    exc,
                )

                await asyncio.sleep(1)

    # ============================================================
    # METRICS
    # ============================================================

    async def _collect_metrics(
        self,
    ) -> Dict[str, Any]:

        provider = self._metrics_provider

        if provider is None:
            return {}

        try:

            result = provider.snapshot()

            if asyncio.iscoroutine(result):
                result = await result

            return result or {}

        except Exception as exc:

            await self._emit_error(
                "backpressure.metrics_failure",
                exc,
            )

            return {}

    # ============================================================
    # RECOMPUTE
    # ============================================================

    async def _recompute(
        self,
        metrics: Dict[str, Any],
    ) -> None:

        queue_depth = int(
            metrics.get("queue_depth", 0)
        )

        inflight = int(
            metrics.get("inflight", 0)
        )

        latency_ms = float(
            metrics.get("latency_ms", 0.0)
        )

        failure_rate = float(
            metrics.get("failure_rate", 0.0)
        )

        queue_score = min(
            1.0,
            queue_depth
            / self._critical_queue_depth,
        )

        latency_score = min(
            1.0,
            latency_ms
            / self._target_latency_ms,
        )

        failure_score = min(
            1.0,
            failure_rate,
        )

        score = (
            (queue_score * 0.5)
            + (latency_score * 0.3)
            + (failure_score * 0.2)
        )

        target_level = (
            PressureLevel.NORMAL
        )

        if (
            queue_depth
            >= self._critical_queue_depth
            or score >= 0.90
        ):
            target_level = (
                PressureLevel.CRITICAL
            )

        elif (
            queue_depth
            >= self._degraded_queue_depth
            or score >= 0.50
        ):
            target_level = (
                PressureLevel.DEGRADED
            )

        now = MONOTONIC()

        async with self._lock:

            current = self._snapshot.level

            if current != target_level:

                elapsed = (
                    now - self._last_transition
                )

                if (
                    elapsed
                    < self._hysteresis_seconds
                ):
                    target_level = current

                else:
                    self._last_transition = now

            concurrency = (
                self._compute_concurrency(
                    target_level,
                    score,
                )
            )

            self._snapshot = PressureSnapshot(
                level=target_level,
                score=score,
                queue_depth=queue_depth,
                inflight=inflight,
                latency_ms=latency_ms,
                failure_rate=failure_rate,
                concurrency_limit=concurrency,
                updated_at=now,
            )

        await self._persist_snapshot()

    # ============================================================
    # CONCURRENCY
    # ============================================================

    def _compute_concurrency(
        self,
        level: PressureLevel,
        score: float,
    ) -> int:

        if level == PressureLevel.NORMAL:
            return self._max_concurrency

        if level == PressureLevel.DEGRADED:

            scale = max(
                0.25,
                1.0 - score,
            )

            return max(
                self._min_concurrency,
                int(
                    self._max_concurrency
                    * scale
                ),
            )

        return self._min_concurrency

    # ============================================================
    # PERSISTENCE
    # ============================================================

    async def _persist_snapshot(
        self,
    ) -> None:

        store = self._state_store

        if store is None:
            return

        snapshot = self._snapshot

        payload = {
            "level": snapshot.level.value,
            "score": snapshot.score,
            "queue_depth": snapshot.queue_depth,
            "inflight": snapshot.inflight,
            "latency_ms": snapshot.latency_ms,
            "failure_rate": snapshot.failure_rate,
            "concurrency_limit": (
                snapshot.concurrency_limit
            ),
            "updated_at": snapshot.updated_at,
        }

        try:

            result = store.set(
                "queue.backpressure",
                payload,
            )

            if asyncio.iscoroutine(result):
                await result

        except Exception as exc:

            await self._emit_error(
                "backpressure.persist_failure",
                exc,
            )

    # ============================================================
    # OBSERVABILITY
    # ============================================================

    async def _emit_metric(
        self,
        event: str,
        payload: Dict[str, Any],
    ) -> None:

        telemetry = self._telemetry

        if telemetry is None:
            return

        try:

            result = telemetry.emit(
                {
                    "event": event,
                    "payload": payload,
                    "ts": MONOTONIC(),
                }
            )

            if asyncio.iscoroutine(result):
                await result

        except Exception:
            pass

    async def _emit_error(
        self,
        event: str,
        exc: Exception,
    ) -> None:

        telemetry = self._telemetry

        if telemetry is None:
            return

        try:

            result = telemetry.emit(
                {
                    "event": event,
                    "error": str(exc),
                    "type": type(exc).__name__,
                    "ts": MONOTONIC(),
                }
            )

            if asyncio.iscoroutine(result):
                await result

        except Exception:
            pass
