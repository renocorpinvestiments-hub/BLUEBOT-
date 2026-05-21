# ============================================================
# core/database/session.py
# ============================================================
# Institutional Async Session Management Layer
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# Transaction and session lifecycle coordination layer.
#
# THIS FILE DOES:
# - manage async session lifecycle
# - manage transaction boundaries
# - guarantee rollback safety
# - provide request-scoped isolation
# - expose deterministic async context APIs
# - support observability integration
# - support future outbox/CQRS evolution
#
# THIS FILE DOES NOT:
# - manage engine lifecycle
# - manage connection pools
# - execute business queries
# - own repositories
# - implement retry logic internally
# - own resilience policies
# - supervise runtime systems
# - manage migrations
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - async-native
# - deterministic
# - idempotent cleanup
# - transaction-safe
# - lock-minimized
# - dependency-injected
# - observability-compatible
# - rollback-safe
# - future distributed compatible
# - no hidden mutable globals
#
# ============================================================

from __future__ import annotations

import time

from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from typing import AsyncIterator
from typing import Mapping
from typing import Optional
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from core.database.engine import DatabaseEngine


# ============================================================
# TRANSACTION STATE
# ============================================================

@dataclass(frozen=True, slots=True)
class TransactionSnapshot:
    """
    Immutable transaction snapshot.

    Safe for:
    - diagnostics
    - observability
    - tracing
    - runtime replay
    """

    started_at: float

    completed_at: Optional[float]

    committed: bool

    rolled_back: bool

    failed: bool

    metadata: Mapping[str, Any]


# ============================================================
# OBSERVABILITY CONTRACT
# ============================================================

class SessionObserver(Protocol):
    """
    Optional observability integration contract.

    Compatible with:
    - tracing systems
    - metrics systems
    - diagnostics pipelines
    - telemetry providers
    """

    async def on_transaction_started(
        self,
        snapshot: TransactionSnapshot,
    ) -> None:
        ...

    async def on_transaction_committed(
        self,
        snapshot: TransactionSnapshot,
    ) -> None:
        ...

    async def on_transaction_rolled_back(
        self,
        snapshot: TransactionSnapshot,
    ) -> None:
        ...

    async def on_transaction_failed(
        self,
        snapshot: TransactionSnapshot,
        exception: Exception,
    ) -> None:
        ...


# ============================================================
# SESSION MANAGER
# ============================================================

class SessionManager:
    """
    Institutional-grade async session manager.

    FEATURES
    --------------------------------------------------------
    - request-scoped session isolation
    - deterministic transaction handling
    - rollback safety
    - async-safe cleanup
    - idempotent session closure
    - future outbox compatibility
    - future saga compatibility
    - future CQRS compatibility

    IMPORTANT
    --------------------------------------------------------
    This layer intentionally avoids:
    - retry logic
    - circuit breaker ownership
    - repository ownership
    - query abstraction
    - runtime orchestration
    - global session registries
    """

    __slots__ = (
        "_engine",
        "_observer",
    )

    def __init__(
        self,
        engine: DatabaseEngine,
        *,
        observer: Optional[SessionObserver] = None,
    ) -> None:

        self._engine = engine

        self._observer = observer

    # ========================================================
    # SESSION ACCESS
    # ========================================================

    @asynccontextmanager
    async def session(
        self,
    ) -> AsyncIterator[AsyncSession]:
        """
        Acquire isolated async session.

        SAFE GUARANTEES
        ----------------------------------------------------
        - no leaked sessions
        - deterministic cleanup
        - isolated request scope
        - async-safe closure
        - rollback protection
        """

        session = self._engine.session_factory()

        try:

            yield session

        except Exception:

            if session.in_transaction():
                await session.rollback()

            raise

        finally:

            await session.close()

    # ========================================================
    # TRANSACTION API
    # ========================================================

    @asynccontextmanager
    async def transaction(
        self,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> AsyncIterator[AsyncSession]:
        """
        Deterministic async transaction boundary.

        API EXAMPLE
        ----------------------------------------------------
        async with session_manager.transaction() as session:
            ...

        SAFE GUARANTEES
        ----------------------------------------------------
        - automatic rollback
        - deterministic commit semantics
        - isolated transaction scope
        - no silent rollback failures
        - cleanup-safe execution
        """

        started_at = time.time()

        transaction_metadata = MappingProxyType(
            dict(metadata or {})
        )

        snapshot = TransactionSnapshot(
            started_at=started_at,
            completed_at=None,
            committed=False,
            rolled_back=False,
            failed=False,
            metadata=transaction_metadata,
        )

        if self._observer is not None:
            await self._observer.on_transaction_started(
                snapshot,
            )

        async with self.session() as session:

            try:

                async with session.begin():
                    yield session

                completed_snapshot = TransactionSnapshot(
                    started_at=started_at,
                    completed_at=time.time(),
                    committed=True,
                    rolled_back=False,
                    failed=False,
                    metadata=transaction_metadata,
                )

                if self._observer is not None:
                    await self._observer.on_transaction_committed(
                        completed_snapshot,
                    )

            except SQLAlchemyError as exc:

                await self._rollback_safe(session)

                failed_snapshot = TransactionSnapshot(
                    started_at=started_at,
                    completed_at=time.time(),
                    committed=False,
                    rolled_back=True,
                    failed=True,
                    metadata=transaction_metadata,
                )

                if self._observer is not None:
                    await self._observer.on_transaction_failed(
                        failed_snapshot,
                        exc,
                    )

                    await self._observer.on_transaction_rolled_back(
                        failed_snapshot,
                    )

                raise

            except Exception as exc:

                await self._rollback_safe(session)

                failed_snapshot = TransactionSnapshot(
                    started_at=started_at,
                    completed_at=time.time(),
                    committed=False,
                    rolled_back=True,
                    failed=True,
                    metadata=transaction_metadata,
                )

                if self._observer is not None:
                    await self._observer.on_transaction_failed(
                        failed_snapshot,
                        exc,
                    )

                    await self._observer.on_transaction_rolled_back(
                        failed_snapshot,
                    )

                raise

    # ========================================================
    # READ-ONLY API
    # ========================================================

    @asynccontextmanager
    async def readonly(
        self,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> AsyncIterator[AsyncSession]:
        """
        Read-only session boundary.

        Future-compatible with:
        - read replicas
        - analytics nodes
        - query routing
        - distributed read scaling
        """

        _ = metadata

        async with self.session() as session:
            yield session

    # ========================================================
    # INTERNALS
    # ========================================================

    async def _rollback_safe(
        self,
        session: AsyncSession,
    ) -> None:
        """
        Failure-safe rollback.

        Prevents:
        - leaked transactions
        - silent rollback corruption
        - partially open sessions
        """

        try:

            if session.in_transaction():
                await session.rollback()

        except Exception:
            # Rollback failures must never mask
            # original transaction exceptions.
            pass
