# ============================================================
# core/database/engine.py
# ============================================================
# Institutional Async Database Engine
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# Async-native database connectivity coordination layer.
#
# THIS FILE DOES:
# - manage async engine lifecycle
# - manage async connection pooling
# - expose health/readiness state
# - coordinate pool initialization safely
# - provide deterministic engine access
# - support future replica routing
# - support observability hooks
# - support resilience integration
#
# THIS FILE DOES NOT:
# - execute business queries
# - contain repositories
# - own transactions
# - manage migrations
# - implement retries internally
# - supervise runtime systems
# - own configuration loading
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - async-first
# - idempotent initialization
# - deterministic lifecycle
# - immutable runtime snapshots
# - dependency-injection friendly
# - observability-compatible
# - distributed-system ready
# - lock-minimized
# - future replica compatible
# - low-overhead hot path
#
# ============================================================

from __future__ import annotations

import asyncio
import time

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any
from typing import Mapping
from typing import Optional
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine.url import URL
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


# ============================================================
# ENGINE STATE
# ============================================================

class EngineState(str, Enum):
    """
    Immutable database engine lifecycle state.
    """

    CREATED = "created"

    INITIALIZING = "initializing"

    READY = "ready"

    DEGRADED = "degraded"

    CLOSED = "closed"

    FAILED = "failed"


# ============================================================
# CONFIG CONTRACT
# ============================================================

@dataclass(frozen=True, slots=True)
class DatabaseEngineConfig:
    """
    Immutable database engine configuration.

    Safe for:
    - hashing
    - runtime snapshots
    - drift detection
    - distributed coordination
    """

    url: str | URL

    echo: bool = False

    pool_size: int = 20

    max_overflow: int = 10

    pool_timeout: float = 30.0

    pool_recycle: int = 1800

    pool_pre_ping: bool = True

    connect_timeout: float = 10.0

    command_timeout: float = 30.0

    healthcheck_timeout: float = 5.0

    idle_transaction_timeout: int = 300

    statement_cache_size: int = 0

    future: bool = True

    def __post_init__(self) -> None:

        if self.pool_size <= 0:
            raise ValueError("pool_size must be greater than zero")

        if self.max_overflow < 0:
            raise ValueError("max_overflow cannot be negative")

        if self.pool_timeout <= 0:
            raise ValueError("pool_timeout must be positive")

        if self.connect_timeout <= 0:
            raise ValueError("connect_timeout must be positive")

        if self.healthcheck_timeout <= 0:
            raise ValueError("healthcheck_timeout must be positive")


# ============================================================
# HEALTH SNAPSHOT
# ============================================================

@dataclass(frozen=True, slots=True)
class DatabaseHealthSnapshot:
    """
    Immutable database health snapshot.
    """

    state: EngineState

    initialized: bool

    pool_size: int

    checked_in_connections: int

    checked_out_connections: int

    overflow_connections: int

    current_overflow: int

    last_healthcheck_at: Optional[float]

    latency_ms: Optional[float]

    metadata: Mapping[str, Any]


# ============================================================
# OBSERVABILITY CONTRACT
# ============================================================

class DatabaseObserver(Protocol):
    """
    Optional observability integration contract.

    Compatible with:
    - metrics systems
    - tracing systems
    - diagnostics
    - telemetry providers
    """

    async def on_engine_initialized(
        self,
        snapshot: DatabaseHealthSnapshot,
    ) -> None:
        ...

    async def on_engine_closed(
        self,
        snapshot: DatabaseHealthSnapshot,
    ) -> None:
        ...

    async def on_healthcheck(
        self,
        snapshot: DatabaseHealthSnapshot,
    ) -> None:
        ...


# ============================================================
# DATABASE ENGINE
# ============================================================

class DatabaseEngine:
    """
    Institutional-grade async database engine.

    FEATURES
    --------------------------------------------------------
    - async-safe initialization
    - idempotent startup
    - deterministic shutdown
    - future replica compatibility
    - health-check support
    - low-overhead engine access
    - connection pool isolation
    - lock-minimized fast paths

    IMPORTANT
    --------------------------------------------------------
    This class intentionally does NOT:
    - manage repositories
    - manage migrations
    - manage transactions
    - retry internally
    - mutate runtime globals
    """

    __slots__ = (
        "_config",
        "_engine",
        "_session_factory",
        "_observer",
        "_state",
        "_initialize_lock",
        "_close_lock",
        "_initialized",
        "_last_healthcheck_at",
        "_last_latency_ms",
    )

    def __init__(
        self,
        config: DatabaseEngineConfig,
        *,
        observer: Optional[DatabaseObserver] = None,
    ) -> None:

        self._config = config

        self._observer = observer

        self._engine: Optional[AsyncEngine] = None

        self._session_factory: Optional[
            async_sessionmaker[AsyncSession]
        ] = None

        self._state = EngineState.CREATED

        self._initialize_lock = asyncio.Lock()

        self._close_lock = asyncio.Lock()

        self._initialized = False

        self._last_healthcheck_at: Optional[float] = None

        self._last_latency_ms: Optional[float] = None

    # ========================================================
    # PROPERTIES
    # ========================================================

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def state(self) -> EngineState:
        return self._state

    @property
    def engine(self) -> AsyncEngine:
        """
        Fast-path engine accessor.
        """

        engine = self._engine

        if engine is None:
            raise RuntimeError(
                "Database engine has not been initialized"
            )

        return engine

    @property
    def session_factory(
        self,
    ) -> async_sessionmaker[AsyncSession]:
        """
        Async session factory accessor.
        """

        factory = self._session_factory

        if factory is None:
            raise RuntimeError(
                "Database session factory unavailable"
            )

        return factory

    # ========================================================
    # INITIALIZATION
    # ========================================================

    async def initialize(self) -> None:
        """
        Idempotent engine initialization.

        SAFE GUARANTEES
        ----------------------------------------------------
        - no duplicate pools
        - no connection leaks
        - no runtime drift
        - deterministic initialization
        - concurrent-safe startup
        """

        if self._initialized:
            return

        async with self._initialize_lock:

            if self._initialized:
                return

            self._state = EngineState.INITIALIZING

            try:

                engine = create_async_engine(
                    self._config.url,
                    echo=self._config.echo,
                    future=self._config.future,
                    pool_size=self._config.pool_size,
                    max_overflow=self._config.max_overflow,
                    pool_timeout=self._config.pool_timeout,
                    pool_recycle=self._config.pool_recycle,
                    pool_pre_ping=self._config.pool_pre_ping,
                    connect_args={
                        "server_settings": {
                            "statement_timeout": str(
                                int(
                                    self._config.command_timeout
                                    * 1000
                                )
                            ),
                            "idle_in_transaction_session_timeout": str(
                                self._config.idle_transaction_timeout
                                * 1000
                            ),
                        }
                    },
                )

                session_factory = async_sessionmaker(
                    bind=engine,
                    autoflush=False,
                    expire_on_commit=False,
                    class_=AsyncSession,
                )

                self._engine = engine

                self._session_factory = session_factory

                await self._verify_connectivity()

                self._initialized = True

                self._state = EngineState.READY

                if self._observer is not None:
                    await self._observer.on_engine_initialized(
                        self.health_snapshot(),
                    )

            except Exception:

                self._state = EngineState.FAILED

                await self._safe_dispose()

                raise

    # ========================================================
    # HEALTH
    # ========================================================

    async def healthcheck(self) -> DatabaseHealthSnapshot:
        """
        Lightweight connectivity verification.

        Designed for:
        - readiness probes
        - orchestration checks
        - runtime diagnostics
        - observability pipelines
        """

        started = time.perf_counter()

        try:

            async with asyncio.timeout(
                self._config.healthcheck_timeout,
            ):

                async with self.connection() as connection:
                    await connection.execute(text("SELECT 1"))

            latency_ms = (
                time.perf_counter() - started
            ) * 1000

            self._last_healthcheck_at = time.time()

            self._last_latency_ms = latency_ms

            self._state = EngineState.READY

        except Exception:

            self._state = EngineState.DEGRADED

            raise

        snapshot = self.health_snapshot()

        if self._observer is not None:
            await self._observer.on_healthcheck(snapshot)

        return snapshot

    def health_snapshot(self) -> DatabaseHealthSnapshot:
        """
        Immutable runtime health snapshot.
        """

        engine = self._engine

        if engine is None:
            return DatabaseHealthSnapshot(
                state=self._state,
                initialized=False,
                pool_size=0,
                checked_in_connections=0,
                checked_out_connections=0,
                overflow_connections=0,
                current_overflow=0,
                last_healthcheck_at=self._last_healthcheck_at,
                latency_ms=self._last_latency_ms,
                metadata=MappingProxyType({}),
            )

        pool = engine.pool

        return DatabaseHealthSnapshot(
            state=self._state,
            initialized=self._initialized,
            pool_size=pool.size(),
            checked_in_connections=pool.checkedin(),
            checked_out_connections=pool.checkedout(),
            overflow_connections=pool.overflow(),
            current_overflow=pool.overflow(),
            last_healthcheck_at=self._last_healthcheck_at,
            latency_ms=self._last_latency_ms,
            metadata=MappingProxyType(
                {
                    "dialect": engine.dialect.name,
                    "driver": engine.dialect.driver,
                }
            ),
        )

    # ========================================================
    # CONNECTION ACCESS
    # ========================================================

    async def connection(self) -> AsyncConnection:
        """
        Acquire raw async connection.

        Designed for:
        - repositories
        - migration systems
        - advanced transaction control
        - analytics workloads
        """

        return await self.engine.connect()

    # ========================================================
    # INTERNALS
    # ========================================================

    async def _verify_connectivity(self) -> None:
        """
        Verify engine connectivity before readiness.
        """

        async with asyncio.timeout(
            self._config.connect_timeout,
        ):

            async with self.connection() as connection:
                await connection.execute(text("SELECT 1"))

    async def _safe_dispose(self) -> None:
        """
        Failure-safe disposal.
        """

        engine = self._engine

        self._engine = None

        self._session_factory = None

        self._initialized = False

        if engine is None:
            return

        try:
            await engine.dispose()
        except Exception:
            pass

    # ========================================================
    # SHUTDOWN
    # ========================================================

    async def close(self) -> None:
        """
        Deterministic engine shutdown.

        SAFE GUARANTEES
        ----------------------------------------------------
        - idempotent close
        - graceful pool cleanup
        - connection disposal
        - async-safe shutdown
        - no duplicate disposal
        """

        if self._state == EngineState.CLOSED:
            return

        async with self._close_lock:

            if self._state == EngineState.CLOSED:
                return

            snapshot = self.health_snapshot()

            await self._safe_dispose()

            self._state = EngineState.CLOSED

            if self._observer is not None:
                await self._observer.on_engine_closed(
                    snapshot,
                )

    # ========================================================
    # CONTEXT MANAGEMENT
    # ========================================================

    async def __aenter__(self) -> "DatabaseEngine":

        await self.initialize()

        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc: Any,
        tb: Any,
    ) -> None:

        await self.close()
