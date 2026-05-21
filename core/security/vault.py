# core/security/vault.py
"""
RENOCORP — Institutional Secret Vault Layer
================================================

Purpose:
    Secret management ONLY.

Responsibilities:
    - Secret retrieval
    - Secret provider abstraction
    - Immutable secret snapshots
    - Secret caching
    - Rotation compatibility
    - Runtime-safe secret access

Non-Responsibilities:
    - Authentication
    - Authorization
    - Config loading
    - Retry logic
    - Metrics implementation
    - Runtime orchestration
    - Persistence
    - Encryption orchestration

Architecture Principles:
    - Immutable
    - Stateless access
    - Async-native
    - Provider-driven
    - Dependency-injected
    - Cache-aware
    - Rotation-compatible
    - Horizontally scalable
    - Distributed-runtime compatible
    - Zero secret leakage
"""

from __future__ import annotations

import asyncio
import os
import time

from dataclasses import dataclass
from pathlib import Path
from typing import (
    Final,
    Mapping,
    Optional,
    Protocol,
    runtime_checkable,
)

# ============================================================
# CONSTANTS
# ============================================================

_DEFAULT_CACHE_TTL: Final[int] = 60
_DEFAULT_MAX_CACHE_SIZE: Final[int] = 10_000

# ============================================================
# SECRET SNAPSHOT
# ============================================================


@dataclass(frozen=True, slots=True)
class SecretSnapshot:
    """
    Immutable secret snapshot.

    Security Properties:
        - immutable
        - async-safe
        - cache-safe
        - repr-safe
    """

    key: str
    value: str
    version: Optional[str]
    loaded_at: int

    def __repr__(self) -> str:
        return (
            "SecretSnapshot("
            "key='***', "
            "value='***', "
            "version='***'"
            ")"
        )


# ============================================================
# EXCEPTIONS
# ============================================================


class VaultError(Exception):
    __slots__ = ()


class SecretNotFoundError(VaultError):
    __slots__ = ()


class SecretProviderError(VaultError):
    __slots__ = ()


class SecretAccessTimeoutError(VaultError):
    __slots__ = ()


# ============================================================
# PROVIDER CONTRACTS
# ============================================================


@runtime_checkable
class SecretProvider(Protocol):
    """
    Secret provider abstraction.
    """

    async def get_secret(
        self,
        key: str,
    ) -> SecretSnapshot:
        ...


# ============================================================
# OPTIONAL OBSERVER CONTRACT
# ============================================================


@runtime_checkable
class VaultObserver(Protocol):
    """
    Observability integration hook.

    Metrics/tracing are handled externally.
    """

    async def on_secret_access(
        self,
        *,
        key: str,
        provider: str,
        cache_hit: bool,
    ) -> None:
        ...


# ============================================================
# INTERNAL TTL CACHE
# ============================================================


class _SecretCache:
    """
    Immutable TTL secret cache.

    Features:
        - lock-minimized
        - monotonic timing
        - bounded memory
        - async-safe
        - no background tasks
    """

    __slots__ = (
        "_ttl_seconds",
        "_max_size",
        "_store",
        "_lock",
    )

    def __init__(
        self,
        *,
        ttl_seconds: int = _DEFAULT_CACHE_TTL,
        max_size: int = _DEFAULT_MAX_CACHE_SIZE,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_size = max_size

        self._store: dict[
            str,
            tuple[float, SecretSnapshot],
        ] = {}

        self._lock = asyncio.Lock()

    async def get(
        self,
        key: str,
    ) -> Optional[SecretSnapshot]:
        cached = self._store.get(key)

        if cached is None:
            return None

        expires_at, snapshot = cached

        if expires_at <= time.monotonic():
            self._store.pop(key, None)
            return None

        return snapshot

    async def set(
        self,
        key: str,
        value: SecretSnapshot,
    ) -> None:
        if len(self._store) >= self._max_size:
            async with self._lock:
                self._evict_expired_locked()

                if len(self._store) >= self._max_size:
                    self._store.pop(
                        next(iter(self._store)),
                        None,
                    )

        self._store[key] = (
            time.monotonic() + self._ttl_seconds,
            value,
        )

    async def invalidate(
        self,
        key: str,
    ) -> None:
        self._store.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    def _evict_expired_locked(self) -> None:
        now = time.monotonic()

        expired = [
            key
            for key, (expires_at, _) in self._store.items()
            if expires_at <= now
        ]

        for key in expired:
            self._store.pop(key, None)


# ============================================================
# VAULT MANAGER
# ============================================================


class VaultManager:
    """
    Institutional-grade vault manager.

    Features:
        - immutable snapshots
        - provider abstraction
        - async-native
        - cache-aware
        - secret rotation compatibility
        - request collapse protection
        - idempotent secret access
        - distributed-runtime compatible

    Guarantees:
        - no secret mutation
        - no global state
        - no secret logging
        - deterministic behavior
    """

    __slots__ = (
        "_provider",
        "_observer",
        "_cache",
        "_timeout_seconds",
        "_inflight",
        "_inflight_lock",
    )

    def __init__(
        self,
        *,
        provider: SecretProvider,
        observer: Optional[VaultObserver] = None,
        cache_ttl_seconds: int = _DEFAULT_CACHE_TTL,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self._provider = provider
        self._observer = observer

        self._cache = _SecretCache(
            ttl_seconds=cache_ttl_seconds,
        )

        self._timeout_seconds = timeout_seconds

        # Prevent provider stampede under concurrency.
        self._inflight: dict[str, asyncio.Future] = {}
        self._inflight_lock = asyncio.Lock()

    # ========================================================
    # PUBLIC API
    # ========================================================

    async def get_secret(
        self,
        key: str,
        *,
        refresh: bool = False,
    ) -> SecretSnapshot:
        """
        Retrieve immutable secret snapshot.

        Guarantees:
            - idempotent
            - async-safe
            - cache-aware
            - side-effect controlled
        """

        normalized_key = self._normalize_key(key)

        if not refresh:
            cached = await self._cache.get(normalized_key)

            if cached is not None:
                await self._emit_access(
                    key=normalized_key,
                    cache_hit=True,
                )

                return cached

        future = await self._acquire_inflight(
            normalized_key,
        )

        if future.done():
            return future.result()

        try:
            snapshot = await self._load_secret(
                normalized_key,
            )

            await self._cache.set(
                normalized_key,
                snapshot,
            )

            future.set_result(snapshot)

            await self._emit_access(
                key=normalized_key,
                cache_hit=False,
            )

            return snapshot

        except Exception as exc:
            future.set_exception(exc)
            raise

        finally:
            async with self._inflight_lock:
                self._inflight.pop(
                    normalized_key,
                    None,
                )

    async def get_secret_value(
        self,
        key: str,
        *,
        refresh: bool = False,
    ) -> str:
        """
        Fast-path value accessor.
        """

        snapshot = await self.get_secret(
            key,
            refresh=refresh,
        )

        return snapshot.value

    async def invalidate(
        self,
        key: str,
    ) -> None:
        """
        Invalidate cached secret.

        Useful for:
            - key rotation
            - hot reload
            - runtime refresh
        """

        normalized_key = self._normalize_key(key)

        await self._cache.invalidate(
            normalized_key,
        )

    async def clear_cache(self) -> None:
        """
        Clear all cached secrets.
        """

        await self._cache.clear()

    # ========================================================
    # INTERNALS
    # ========================================================

    async def _load_secret(
        self,
        key: str,
    ) -> SecretSnapshot:
        try:
            if self._timeout_seconds is None:
                return await self._provider.get_secret(
                    key,
                )

            return await asyncio.wait_for(
                self._provider.get_secret(key),
                timeout=self._timeout_seconds,
            )

        except asyncio.TimeoutError as exc:
            raise SecretAccessTimeoutError(
                "secret_access_timeout",
            ) from exc

    async def _acquire_inflight(
        self,
        key: str,
    ) -> asyncio.Future:
        async with self._inflight_lock:
            existing = self._inflight.get(key)

            if existing is not None:
                return await existing

            future: asyncio.Future = (
                asyncio.get_running_loop()
                .create_future()
            )

            self._inflight[key] = future

            return future

    async def _emit_access(
        self,
        *,
        key: str,
        cache_hit: bool,
    ) -> None:
        if self._observer is None:
            return

        await self._observer.on_secret_access(
            key=key,
            provider=type(self._provider).__name__,
            cache_hit=cache_hit,
        )

    @staticmethod
    def _normalize_key(
        key: str,
    ) -> str:
        normalized = key.strip()

        if not normalized:
            raise SecretNotFoundError(
                "invalid_secret_key",
            )

        return normalized


# ============================================================
# CHAIN PROVIDER
# ============================================================


class ChainProvider:
    """
    Sequential provider fallback.

    Supports:
        - multi-provider failover
        - cloud migration
        - staged rotation
        - hybrid secret backends
    """

    __slots__ = ("_providers",)

    def __init__(
        self,
        providers: tuple[SecretProvider, ...],
    ) -> None:
        self._providers = providers

    async def get_secret(
        self,
        key: str,
    ) -> SecretSnapshot:
        last_error: Optional[Exception] = None

        for provider in self._providers:
            try:
                return await provider.get_secret(key)

            except SecretNotFoundError as exc:
                last_error = exc

        raise SecretNotFoundError(
            f"secret_not_found:{key}"
        ) from last_error


# ============================================================
# ENV PROVIDER
# ============================================================


class EnvProvider:
    """
    Environment secret provider.
    """

    __slots__ = ()

    async def get_secret(
        self,
        key: str,
    ) -> SecretSnapshot:
        value = os.getenv(key)

        if value is None:
            raise SecretNotFoundError(
                f"secret_not_found:{key}"
            )

        return SecretSnapshot(
            key=key,
            value=value,
            version=None,
            loaded_at=int(time.time()),
        )


# ============================================================
# FILE PROVIDER
# ============================================================


class FileProvider:
    """
    Immutable file-backed secret provider.
    """

    __slots__ = (
        "_base_path",
    )

    def __init__(
        self,
        *,
        base_path: str,
    ) -> None:
        self._base_path = Path(base_path).resolve()

    async def get_secret(
        self,
        key: str,
    ) -> SecretSnapshot:
        safe_key = key.replace("..", "")

        path = (
            self._base_path / safe_key
        ).resolve()

        if self._base_path not in path.parents:
            raise SecretProviderError(
                "invalid_secret_path"
            )

        if not path.exists():
            raise SecretNotFoundError(
                f"secret_not_found:{key}"
            )

        try:
            value = await asyncio.to_thread(
                path.read_text,
                encoding="utf-8",
            )

        except Exception as exc:
            raise SecretProviderError(
                "secret_read_failure",
            ) from exc

        return SecretSnapshot(
            key=key,
            value=value.strip(),
            version=None,
            loaded_at=int(time.time()),
        )


# ============================================================
# STATIC PROVIDER
# ============================================================


class StaticProvider:
    """
    Immutable in-memory provider.
    """

    __slots__ = (
        "_secrets",
    )

    def __init__(
        self,
        secrets: Mapping[str, str],
    ) -> None:
        self._secrets = dict(secrets)

    async def get_secret(
        self,
        key: str,
    ) -> SecretSnapshot:
        value = self._secrets.get(key)

        if value is None:
            raise SecretNotFoundError(
                f"secret_not_found:{key}"
            )

        return SecretSnapshot(
            key=key,
            value=value,
            version=None,
            loaded_at=int(time.time()),
        )


# ============================================================
# PUBLIC EXPORTS
# ============================================================

__all__ = (
    "SecretSnapshot",
    "VaultError",
    "SecretNotFoundError",
    "SecretProviderError",
    "SecretAccessTimeoutError",
    "SecretProvider",
    "VaultObserver",
    "VaultManager",
    "ChainProvider",
    "EnvProvider",
    "FileProvider",
    "StaticProvider",
    )
