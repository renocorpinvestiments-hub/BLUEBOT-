"""
core/discovery/scanner.py

RENOCORP Institutional Discovery Scanner
========================================

Purpose
-------
High-performance async discovery engine responsible ONLY for:

- locating modules
- namespace-safe discovery
- lazy scanning
- capability detection
- package normalization
- immutable discovery snapshots

This module DOES NOT:
- import plugins
- execute plugins
- validate manifests
- manage runtime state
- perform orchestration

Design Goals
------------
- async-first
- idempotent
- lock-minimized
- scalable
- deterministic
- plugin-safe
- runtime-safe
- future distributed-ready

Compatible With
----------------
- AI systems
- trading systems
- web applications
- microservices
- event-driven runtimes
- plugin ecosystems
- distributed workers

Architecture Notes
------------------
This scanner intentionally avoids:
- importlib execution
- runtime mutation
- global state coupling
- eager loading
- blocking scans

Future-Compatible With
----------------------
- zip plugins
- remote manifests
- cloud discovery
- WASM modules
- distributed runtimes
- hot discovery
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
)

# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTS
# ============================================================

DEFAULT_SCAN_EXTENSIONS: FrozenSet[str] = frozenset(
    {
        ".py",
        ".pyc",
        ".pyd",
        ".so",
    }
)

DEFAULT_EXCLUDED_DIRS: FrozenSet[str] = frozenset(
    {
        "__pycache__",
        ".git",
        ".idea",
        ".vscode",
        ".pytest_cache",
        "node_modules",
        "venv",
        ".venv",
        "dist",
        "build",
    }
)

# ============================================================
# DATA MODELS
# ============================================================


@dataclass(frozen=True, slots=True)
class NamespacePolicy:
    """
    Immutable namespace filtering policy.

    Controls:
    - allowed namespaces
    - blocked namespaces
    - internal-only scopes
    """

    allowed_roots: FrozenSet[str] = frozenset()
    blocked_namespaces: FrozenSet[str] = frozenset()
    internal_prefixes: FrozenSet[str] = frozenset({"_"})

    def is_allowed(self, namespace: str) -> bool:
        """
        Determine whether a namespace is allowed.
        """

        namespace = namespace.strip()

        if not namespace:
            return False

        for blocked in self.blocked_namespaces:
            if namespace.startswith(blocked):
                return False

        for prefix in self.internal_prefixes:
            if namespace.split(".")[-1].startswith(prefix):
                return False

        if not self.allowed_roots:
            return True

        return any(
            namespace.startswith(root)
            for root in self.allowed_roots
        )


@dataclass(frozen=True, slots=True)
class DiscoveredModule:
    """
    Immutable discovered module descriptor.
    """

    namespace: str
    file_path: str
    relative_path: str
    extension: str
    checksum: str
    size_bytes: int
    modified_at: float
    capabilities: FrozenSet[str] = frozenset()


@dataclass(frozen=True, slots=True)
class DiscoverySnapshot:
    """
    Immutable discovery snapshot.

    Safe for:
    - async sharing
    - caching
    - observability
    - diagnostics
    """

    discovered_modules: Tuple[DiscoveredModule, ...]
    scanned_roots: Tuple[str, ...]
    duration_ms: float
    total_files_scanned: int
    created_at: float
    scan_id: str

    @property
    def total_modules(self) -> int:
        return len(self.discovered_modules)


# ============================================================
# DISCOVERY SCANNER
# ============================================================


class DiscoveryScanner:
    """
    Institutional-grade async discovery scanner.

    Responsibilities:
    - async-safe scanning
    - deterministic discovery
    - duplicate prevention
    - immutable snapshots
    - namespace normalization
    - lazy checksum generation

    Non-Responsibilities:
    - plugin execution
    - runtime orchestration
    - manifest validation
    - dependency loading
    """

    def __init__(
        self,
        *,
        roots: Iterable[str | Path],
        namespace_policy: Optional[NamespacePolicy] = None,
        allowed_extensions: FrozenSet[str] = DEFAULT_SCAN_EXTENSIONS,
        excluded_dirs: FrozenSet[str] = DEFAULT_EXCLUDED_DIRS,
        max_concurrency: int = 32,
        enable_checksums: bool = True,
        follow_symlinks: bool = False,
    ) -> None:

        self._roots: Tuple[Path, ...] = tuple(
            Path(root).resolve()
            for root in roots
        )

        self._policy = namespace_policy or NamespacePolicy()

        self._allowed_extensions = allowed_extensions
        self._excluded_dirs = excluded_dirs

        self._enable_checksums = enable_checksums
        self._follow_symlinks = follow_symlinks

        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))

        self._cache: Dict[str, DiscoveredModule] = {}

        self._last_snapshot: Optional[DiscoverySnapshot] = None

    # ========================================================
    # PUBLIC API
    # ========================================================

    async def scan(self) -> DiscoverySnapshot:
        """
        Execute deterministic async discovery scan.

        Guarantees:
        - idempotent output
        - no runtime mutation
        - duplicate prevention
        - immutable results
        """

        start = time.perf_counter()

        discovered: Dict[str, DiscoveredModule] = {}

        tasks = [
            self._scan_root(root, discovered)
            for root in self._roots
        ]

        await asyncio.gather(*tasks)

        modules = tuple(
            sorted(
                discovered.values(),
                key=lambda m: m.namespace,
            )
        )

        duration_ms = (
            time.perf_counter() - start
        ) * 1000.0

        snapshot = DiscoverySnapshot(
            discovered_modules=modules,
            scanned_roots=tuple(str(r) for r in self._roots),
            duration_ms=duration_ms,
            total_files_scanned=len(discovered),
            created_at=time.time(),
            scan_id=self._generate_scan_id(modules),
        )

        self._last_snapshot = snapshot

        logger.info(
            "Discovery scan completed | modules=%s duration_ms=%.2f",
            snapshot.total_modules,
            snapshot.duration_ms,
        )

        return snapshot

    def snapshot(self) -> Optional[DiscoverySnapshot]:
        """
        Return latest immutable snapshot.
        """

        return self._last_snapshot

    def clear_cache(self) -> None:
        """
        Clear internal scan cache.
        """

        self._cache.clear()

    # ========================================================
    # INTERNAL SCANNING
    # ========================================================

    async def _scan_root(
        self,
        root: Path,
        discovered: Dict[str, DiscoveredModule],
    ) -> None:
        """
        Scan a single discovery root.
        """

        if not root.exists():
            logger.warning(
                "Discovery root does not exist: %s",
                root,
            )
            return

        if not root.is_dir():
            logger.warning(
                "Discovery root is not a directory: %s",
                root,
            )
            return

        await self._walk_directory(root, root, discovered)

    async def _walk_directory(
        self,
        root: Path,
        current: Path,
        discovered: Dict[str, DiscoveredModule],
    ) -> None:
        """
        Async-safe recursive directory walk.
        """

        try:
            entries = await asyncio.to_thread(
                lambda: list(current.iterdir())
            )

        except Exception as exc:
            logger.exception(
                "Failed to iterate directory: %s",
                current,
                exc_info=exc,
            )
            return

        tasks: List[asyncio.Task] = []

        for entry in entries:

            if entry.name in self._excluded_dirs:
                continue

            try:

                if entry.is_symlink() and not self._follow_symlinks:
                    continue

                if entry.is_dir():

                    tasks.append(
                        asyncio.create_task(
                            self._walk_directory(
                                root,
                                entry,
                                discovered,
                            )
                        )
                    )

                    continue

                if not entry.is_file():
                    continue

                if entry.suffix not in self._allowed_extensions:
                    continue

                tasks.append(
                    asyncio.create_task(
                        self._process_file(
                            root,
                            entry,
                            discovered,
                        )
                    )
                )

            except Exception as exc:
                logger.exception(
                    "Directory traversal failure: %s",
                    entry,
                    exc_info=exc,
                )

        if tasks:
            await asyncio.gather(*tasks)

    # ========================================================
    # FILE PROCESSING
    # ========================================================

    async def _process_file(
        self,
        root: Path,
        file_path: Path,
        discovered: Dict[str, DiscoveredModule],
    ) -> None:
        """
        Process individual file safely.
        """

        async with self._semaphore:

            try:

                resolved = file_path.resolve()

                if not self._is_safe_path(root, resolved):
                    return

                namespace = self._build_namespace(
                    root,
                    resolved,
                )

                if not namespace:
                    return

                if not self._policy.is_allowed(namespace):
                    return

                if namespace in discovered:
                    return

                stat = await asyncio.to_thread(
                    resolved.stat
                )

                checksum = ""

                if self._enable_checksums:
                    checksum = await self._generate_checksum(
                        resolved
                    )

                capabilities = self._detect_capabilities(
                    namespace
                )

                module = DiscoveredModule(
                    namespace=namespace,
                    file_path=str(resolved),
                    relative_path=str(
                        resolved.relative_to(root)
                    ),
                    extension=resolved.suffix,
                    checksum=checksum,
                    size_bytes=stat.st_size,
                    modified_at=stat.st_mtime,
                    capabilities=capabilities,
                )

                discovered[namespace] = module

                self._cache[namespace] = module

            except Exception as exc:
                logger.exception(
                    "Failed processing file: %s",
                    file_path,
                    exc_info=exc,
                )

    # ========================================================
    # NAMESPACE UTILITIES
    # ========================================================

    def _build_namespace(
        self,
        root: Path,
        file_path: Path,
    ) -> Optional[str]:
        """
        Convert filesystem path into normalized namespace.
        """

        try:

            relative = file_path.relative_to(root)

            parts = list(relative.parts)

            if not parts:
                return None

            filename = parts[-1]

            if filename.startswith("_"):
                return None

            stem = Path(filename).stem

            if stem == "__init__":
                parts = parts[:-1]
            else:
                parts[-1] = stem

            namespace = ".".join(parts)

            return namespace.replace("\\", ".")

        except Exception:
            return None

    # ========================================================
    # CAPABILITY DETECTION
    # ========================================================

    def _detect_capabilities(
        self,
        namespace: str,
    ) -> FrozenSet[str]:
        """
        Lightweight capability inference.

        IMPORTANT:
        This does NOT import modules.
        """

        capabilities: Set[str] = set()

        lowered = namespace.lower()

        if "worker" in lowered:
            capabilities.add("WORKER")

        if "event" in lowered:
            capabilities.add("EVENT_CONSUMER")

        if "queue" in lowered:
            capabilities.add("QUEUE_HANDLER")

        if "telemetry" in lowered:
            capabilities.add("TELEMETRY_PROVIDER")

        if "strategy" in lowered:
            capabilities.add("STRATEGY")

        return frozenset(capabilities)

    # ========================================================
    # SAFETY
    # ========================================================

    def _is_safe_path(
        self,
        root: Path,
        candidate: Path,
    ) -> bool:
        """
        Prevent path escape attacks.
        """

        try:
            candidate.relative_to(root)
            return True

        except Exception:
            logger.warning(
                "Rejected unsafe path: %s",
                candidate,
            )
            return False

    # ========================================================
    # CHECKSUMS
    # ========================================================

    async def _generate_checksum(
        self,
        path: Path,
    ) -> str:
        """
        Generate deterministic SHA256 checksum.
        """

        return await asyncio.to_thread(
            self._checksum_sync,
            path,
        )

    @staticmethod
    def _checksum_sync(path: Path) -> str:

        hasher = hashlib.sha256()

        with path.open("rb") as f:

            while True:

                chunk = f.read(1024 * 1024)

                if not chunk:
                    break

                hasher.update(chunk)

        return hasher.hexdigest()

    # ========================================================
    # SNAPSHOT IDS
    # ========================================================

    def _generate_scan_id(
        self,
        modules: Tuple[DiscoveredModule, ...],
    ) -> str:
        """
        Generate deterministic scan identifier.
        """

        hasher = hashlib.sha256()

        for module in modules:

            hasher.update(
                module.namespace.encode("utf-8")
            )

            hasher.update(
                module.checksum.encode("utf-8")
            )

        return hasher.hexdigest()

    # ========================================================
    # CLEANUP
    # ========================================================

    async def shutdown(self) -> None:
        """
        Graceful scanner cleanup.

        Keeps lifecycle ownership explicit.
        """

        self._cache.clear()

        logger.info(
            "DiscoveryScanner shutdown complete."
        )
