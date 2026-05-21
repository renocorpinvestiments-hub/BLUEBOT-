# ============================================================
# core/cache/backend.py
# ============================================================
# Institutional Cache Storage Backend
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# Low-level cache persistence and storage engine.
#
# THIS FILE DOES:
# - disk persistence
# - atomic writes
# - corruption protection
# - namespace partitioning
# - preload optimization
# - async-safe storage access
# - serialization abstraction
# - memory acceleration
#
# THIS FILE DOES NOT:
# - enforce business rules
# - manage lifecycle orchestration
# - implement cache policy
# - expose public cache APIs
#
# Those responsibilities belong to:
# - cache/manager.py
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - RAM-first acceleration
# - disk-backed persistence
# - deterministic serialization
# - atomic filesystem operations
# - future backend extensibility
# - corruption resistance
# - scalable namespace isolation
#
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Dict, Optional

from .models import CacheEntry


logger = logging.getLogger(__name__)


class CacheBackend:
    """
    Institutional-grade cache backend.

    Responsibilities:
    --------------------------------------------------------
    - persistent storage
    - atomic file operations
    - namespace isolation
    - serialization/deserialization
    - preload acceleration
    - safe file handling
    """

    # ========================================================
    # INITIALIZATION
    # ========================================================

    def __init__(
        self,
        *,
        root_dir: str = "cache_store",
        preload_limit: int = 5000,
    ) -> None:

        self._root = Path(root_dir)

        self._preload_limit = max(preload_limit, 1)

        # ----------------------------------------------------
        # HOT STORAGE ACCELERATION
        # ----------------------------------------------------

        self._memory_cache: Dict[str, Dict[str, CacheEntry]] = {}

        # ----------------------------------------------------
        # ASYNC FILE LOCKS
        # ----------------------------------------------------

        self._locks: Dict[str, asyncio.Lock] = {}

        # ----------------------------------------------------
        # STATE
        # ----------------------------------------------------

        self._initialized = False

    # ========================================================
    # LIFECYCLE
    # ========================================================

    async def initialize(self) -> None:
        """
        Initialize backend storage safely.
        """

        if self._initialized:
            return

        self._root.mkdir(parents=True, exist_ok=True)

        self._initialized = True

        logger.info(
            "Cache backend initialized at: %s",
            self._root.resolve(),
        )

    async def shutdown(self) -> None:
        """
        Graceful backend shutdown.
        """

        self._memory_cache.clear()

        logger.info("Cache backend shutdown complete.")

    # ========================================================
    # PUBLIC STORAGE API
    # ========================================================

    async def load(
        self,
        namespace: str,
        key: str,
    ) -> Optional[CacheEntry]:
        """
        Load cache entry safely.
        """

        # ----------------------------------------------------
        # HOT MEMORY LOOKUP
        # ----------------------------------------------------

        namespace_cache = self._memory_cache.get(namespace)

        if namespace_cache:
            entry = namespace_cache.get(key)

            if entry:
                return entry

        # ----------------------------------------------------
        # DISK LOOKUP
        # ----------------------------------------------------

        path = self._entry_path(namespace, key)

        if not path.exists():
            return None

        lock = self._get_lock(namespace)

        async with lock:

            try:
                payload = await asyncio.to_thread(
                    path.read_text,
                    encoding="utf-8",
                )

                data = json.loads(payload)

                entry = CacheEntry.from_dict(data)

                # --------------------------------------------
                # HOT CACHE PROMOTION
                # --------------------------------------------

                self._memory_cache.setdefault(
                    namespace,
                    {},
                )[key] = entry

                return entry

            except Exception:
                logger.exception(
                    "Failed loading cache entry: %s",
                    path,
                )
                return None

    async def save(
        self,
        namespace: str,
        key: str,
        entry: CacheEntry,
    ) -> None:
        """
        Persist cache entry safely.

        Uses atomic temp-file replacement.
        """

        namespace_dir = self._namespace_dir(namespace)

        namespace_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        path = self._entry_path(namespace, key)

        lock = self._get_lock(namespace)

        async with lock:

            try:

                # --------------------------------------------
                # HOT MEMORY UPDATE
                # --------------------------------------------

                self._memory_cache.setdefault(
                    namespace,
                    {},
                )[key] = entry

                payload = json.dumps(
                    entry.to_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )

                # --------------------------------------------
                # ATOMIC WRITE
                # --------------------------------------------

                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=namespace_dir,
                    delete=False,
                ) as temp_file:

                    temp_file.write(payload)

                    temp_path = Path(temp_file.name)

                # --------------------------------------------
                # SAFE FILE SWAP
                # --------------------------------------------

                temp_path.replace(path)

            except Exception:
                logger.exception(
                    "Failed saving cache entry: %s:%s",
                    namespace,
                    key,
                )
                raise

    async def delete(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """
        Delete cache entry safely.
        """

        lock = self._get_lock(namespace)

        async with lock:

            self._memory_cache.get(
                namespace,
                {},
            ).pop(key, None)

            path = self._entry_path(namespace, key)

            try:
                if path.exists():
                    path.unlink()

            except Exception:
                logger.exception(
                    "Failed deleting cache entry: %s",
                    path,
                )

    async def clear_namespace(
        self,
        namespace: str,
    ) -> None:
        """
        Remove all entries in namespace safely.
        """

        lock = self._get_lock(namespace)

        async with lock:

            self._memory_cache.pop(namespace, None)

            namespace_dir = self._namespace_dir(namespace)

            if not namespace_dir.exists():
                return

            try:

                for file_path in namespace_dir.glob("*.json"):

                    try:
                        file_path.unlink()

                    except Exception:
                        logger.exception(
                            "Failed removing cache file: %s",
                            file_path,
                        )

            except Exception:
                logger.exception(
                    "Failed clearing namespace: %s",
                    namespace,
                )

    # ========================================================
    # PRELOAD / DISCOVERY
    # ========================================================

    async def preload(
        self,
        namespace: str,
    ) -> Dict[str, CacheEntry]:
        """
        Preload namespace entries into RAM.

        Startup acceleration layer.
        """

        namespace_dir = self._namespace_dir(namespace)

        if not namespace_dir.exists():
            return {}

        entries: Dict[str, CacheEntry] = {}

        files = list(namespace_dir.glob("*.json"))

        for file_path in files[: self._preload_limit]:

            try:

                payload = await asyncio.to_thread(
                    file_path.read_text,
                    encoding="utf-8",
                )

                data = json.loads(payload)

                entry = CacheEntry.from_dict(data)

                entries[entry.key] = entry

            except Exception:
                logger.exception(
                    "Failed preloading cache file: %s",
                    file_path,
                )

        self._memory_cache[namespace] = entries

        return entries

    async def list_namespaces(self) -> list[str]:
        """
        Discover available namespaces.
        """

        if not self._root.exists():
            return []

        namespaces = []

        for item in self._root.iterdir():

            if item.is_dir():
                namespaces.append(item.name)

        return sorted(namespaces)

    # ========================================================
    # INTERNAL HELPERS
    # ========================================================

    def _namespace_dir(
        self,
        namespace: str,
    ) -> Path:
        return self._root / namespace

    def _entry_path(
        self,
        namespace: str,
        key: str,
    ) -> Path:

        safe_key = self._sanitize_key(key)

        return self._namespace_dir(namespace) / f"{safe_key}.json"

    @staticmethod
    def _sanitize_key(key: str) -> str:
        """
        Filesystem-safe cache key.
        """

        return (
            key.replace("/", "_")
            .replace("\\", "_")
            .replace(":", "_")
            .replace(" ", "_")
        )

    def _get_lock(
        self,
        namespace: str,
    ) -> asyncio.Lock:

        if namespace not in self._locks:
            self._locks[namespace] = asyncio.Lock()

        return self._locks[namespace]

    # ========================================================
    # OBSERVABILITY
    # ========================================================

    def stats(self) -> dict:
        """
        Lightweight backend statistics.
        """

        return {
            "initialized": self._initialized,
            "namespaces": len(self._memory_cache),
            "memory_entries": sum(
                len(v)
                for v in self._memory_cache.values()
            ),
            "root": str(self._root),
        }
