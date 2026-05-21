# ============================================================
# core/cache/manager.py
# ============================================================
# Institutional Cache Orchestration Layer
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# Centralized cache coordination layer for:
#
# - namespace isolation
# - hot memory caching
# - persistence orchestration
# - TTL enforcement
# - cache invalidation
# - concurrency safety
# - startup warmup
# - idempotent cache access
# - corruption protection
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - RAM-first architecture
# - backend abstraction
# - async-safe operations
# - deterministic behavior
# - zero business logic
# - minimal surface area
# - scalable namespace isolation
#
# IMPORTANT
# ------------------------------------------------------------
# ALL cache access MUST go through this file.
#
# No other module should:
# - write cache files directly
# - serialize temp state
# - create ad-hoc snapshots
# - bypass validation
#
# ============================================================

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import defaultdict
from typing import Any, Dict, Optional, Set

from .backend import CacheBackend
from .models import CacheEntry


logger = logging.getLogger(__name__)


class CacheManager:
    """
    Institutional-grade centralized cache manager.

    Responsibilities:
    --------------------------------------------------------
    - cache coordination
    - namespace isolation
    - validation gateway
    - RAM-first access
    - persistence routing
    - TTL enforcement
    - concurrency safety
    - startup warmup
    - safe invalidation
    """

    # ========================================================
    # INITIALIZATION
    # ========================================================

    def __init__(
        self,
        backend: CacheBackend,
        *,
        default_ttl: int = 3600,
        enable_checksums: bool = True,
        enable_warmup: bool = True,
    ) -> None:

        self._backend = backend

        self._default_ttl = max(default_ttl, 1)

        self._enable_checksums = enable_checksums
        self._enable_warmup = enable_warmup

        # ----------------------------------------------------
        # HOT MEMORY CACHE
        # ----------------------------------------------------

        self._memory: Dict[str, Dict[str, CacheEntry]] = defaultdict(dict)

        # ----------------------------------------------------
        # NAMESPACE LOCKS
        # ----------------------------------------------------

        self._locks: Dict[str, asyncio.Lock] = {}

        # ----------------------------------------------------
        # REGISTERED NAMESPACES
        # ----------------------------------------------------

        self._namespaces: Set[str] = set()

        # ----------------------------------------------------
        # DIRTY TRACKING
        # ----------------------------------------------------

        self._dirty: Set[str] = set()

        # ----------------------------------------------------
        # STATE
        # ----------------------------------------------------

        self._started = False

    # ========================================================
    # LIFECYCLE
    # ========================================================

    async def start(self) -> None:
        """
        Initialize cache manager.
        """

        if self._started:
            return

        logger.info("Starting cache manager...")

        await self._backend.initialize()

        if self._enable_warmup:
            await self._warmup()

        self._started = True

        logger.info("Cache manager started.")

    async def shutdown(self) -> None:
        """
        Graceful shutdown with flush protection.
        """

        if not self._started:
            return

        logger.info("Shutting down cache manager...")

        await self.flush()

        await self._backend.shutdown()

        self._started = False

        logger.info("Cache manager shutdown complete.")

    # ========================================================
    # NAMESPACE MANAGEMENT
    # ========================================================

    def register_namespace(self, namespace: str) -> None:
        """
        Register isolated cache namespace.
        """

        namespace = self._normalize_namespace(namespace)

        if namespace in self._namespaces:
            return

        self._namespaces.add(namespace)

        if namespace not in self._locks:
            self._locks[namespace] = asyncio.Lock()

        logger.debug("Registered cache namespace: %s", namespace)

    # ========================================================
    # PUBLIC API
    # ========================================================

    async def get(
        self,
        namespace: str,
        key: str,
        default: Optional[Any] = None,
    ) -> Optional[Any]:
        """
        Retrieve cached value.

        RAM -> Disk fallback strategy.
        """

        namespace = self._normalize_namespace(namespace)

        self._validate_namespace(namespace)

        # ----------------------------------------------------
        # HOT MEMORY LOOKUP
        # ----------------------------------------------------

        entry = self._memory[namespace].get(key)

        if entry:

            if self._is_expired(entry):
                await self.delete(namespace, key)
                return default

            return entry.value

        # ----------------------------------------------------
        # DISK FALLBACK
        # ----------------------------------------------------

        async with self._locks[namespace]:

            entry = await self._backend.load(namespace, key)

            if not entry:
                return default

            if self._is_expired(entry):
                await self.delete(namespace, key)
                return default

            if self._enable_checksums:
                if not self._validate_checksum(entry):
                    logger.warning(
                        "Checksum validation failed for %s:%s",
                        namespace,
                        key,
                    )
                    await self.delete(namespace, key)
                    return default

            # ------------------------------------------------
            # PROMOTE TO HOT CACHE
            # ------------------------------------------------

            self._memory[namespace][key] = entry

            return entry.value

    async def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        *,
        ttl: Optional[int] = None,
        persist: bool = True,
    ) -> None:
        """
        Store cache entry safely.
        """

        namespace = self._normalize_namespace(namespace)

        self._validate_namespace(namespace)

        ttl = ttl or self._default_ttl

        timestamp = int(time.time())

        checksum = (
            self._generate_checksum(value)
            if self._enable_checksums
            else None
        )

        entry = CacheEntry(
            key=key,
            value=value,
            namespace=namespace,
            timestamp=timestamp,
            ttl=ttl,
            checksum=checksum,
            version=1,
        )

        # ----------------------------------------------------
        # HOT MEMORY WRITE
        # ----------------------------------------------------

        self._memory[namespace][key] = entry

        if not persist:
            return

        # ----------------------------------------------------
        # DIRTY MARKING
        # ----------------------------------------------------

        dirty_key = f"{namespace}:{key}"

        self._dirty.add(dirty_key)

        # ----------------------------------------------------
        # PERSIST
        # ----------------------------------------------------

        async with self._locks[namespace]:
            await self._backend.save(namespace, key, entry)

    async def delete(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """
        Delete cache entry safely.
        """

        namespace = self._normalize_namespace(namespace)

        self._validate_namespace(namespace)

        self._memory[namespace].pop(key, None)

        async with self._locks[namespace]:
            await self._backend.delete(namespace, key)

    async def invalidate_namespace(
        self,
        namespace: str,
    ) -> None:
        """
        Invalidate entire namespace.
        """

        namespace = self._normalize_namespace(namespace)

        self._validate_namespace(namespace)

        logger.warning(
            "Invalidating cache namespace: %s",
            namespace,
        )

        self._memory[namespace].clear()

        async with self._locks[namespace]:
            await self._backend.clear_namespace(namespace)

    async def flush(self) -> None:
        """
        Flush dirty entries to backend safely.
        """

        if not self._dirty:
            return

        logger.info(
            "Flushing %d dirty cache entries...",
            len(self._dirty),
        )

        for dirty_key in list(self._dirty):

            try:
                namespace, key = dirty_key.split(":", 1)

                entry = self._memory[namespace].get(key)

                if not entry:
                    continue

                async with self._locks[namespace]:
                    await self._backend.save(
                        namespace,
                        key,
                        entry,
                    )

                self._dirty.discard(dirty_key)

            except Exception:
                logger.exception(
                    "Failed flushing cache entry: %s",
                    dirty_key,
                )

    # ========================================================
    # INTERNALS
    # ========================================================

    async def _warmup(self) -> None:
        """
        Warm startup-critical cache state.
        """

        logger.info("Running cache warmup...")

        try:
            namespaces = await self._backend.list_namespaces()

            for namespace in namespaces:

                self.register_namespace(namespace)

                entries = await self._backend.preload(namespace)

                self._memory[namespace].update(entries)

            logger.info("Cache warmup completed.")

        except Exception:
            logger.exception("Cache warmup failed.")

    def _validate_namespace(self, namespace: str) -> None:

        if namespace not in self._namespaces:
            raise ValueError(
                f"Unregistered cache namespace: {namespace}"
            )

    @staticmethod
    def _normalize_namespace(namespace: str) -> str:
        return namespace.strip().lower()

    @staticmethod
    def _is_expired(entry: CacheEntry) -> bool:

        if entry.ttl <= 0:
            return False

        now = int(time.time())

        return (now - entry.timestamp) > entry.ttl

    @staticmethod
    def _generate_checksum(value: Any) -> str:

        payload = repr(value).encode("utf-8")

        return hashlib.sha256(payload).hexdigest()

    def _validate_checksum(self, entry: CacheEntry) -> bool:

        if not entry.checksum:
            return False

        checksum = self._generate_checksum(entry.value)

        return checksum == entry.checksum

    # ========================================================
    # OBSERVABILITY
    # ========================================================

    def stats(self) -> Dict[str, Any]:
        """
        Lightweight runtime cache statistics.
        """

        return {
            "started": self._started,
            "namespaces": len(self._namespaces),
            "dirty_entries": len(self._dirty),
            "memory_entries": sum(
                len(v)
                for v in self._memory.values()
            ),
        }


# ============================================================
# FACTORY
# ============================================================

def create_cache_manager(
    backend: CacheBackend,
) -> CacheManager:
    """
    Institutional cache manager factory.
    """

    return CacheManager(backend=backend)
