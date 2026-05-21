"""
core/discovery/loader.py

RENOCORP Institutional Module Loader
====================================

Purpose
-------
Institutional-grade lazy module loading system.

This module ONLY handles:
- lazy imports
- isolated module loading
- capability registration
- deterministic load tracking
- safe initialization boundaries
- rollback-safe registration

This module DOES NOT:
- scan directories
- validate manifests
- orchestrate runtime
- supervise workers
- manage queues
- own lifecycle state

Design Goals
------------
- async-first
- idempotent
- deterministic
- scalable
- safe
- rollback-capable
- plugin-safe
- low-overhead
- distributed-ready

Compatible With
----------------
- AI runtimes
- web frameworks
- trading systems
- automation systems
- SaaS platforms
- distributed workers
- plugin ecosystems
- event-driven runtimes
- microservices

Future Compatible With
-----------------------
- remote loaders
- sandboxed runtimes
- subprocess isolation
- WASM modules
- distributed registries
- containerized execution
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from dataclasses import dataclass, field
from types import ModuleType
from typing import (
    Any,
    Dict,
    FrozenSet,
    Mapping,
    Optional,
    Set,
    Tuple,
)

from core.discovery.manifest import (
    Capability,
    ManifestValidator,
    ModuleManifest,
)

# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)

# ============================================================
# EXCEPTIONS
# ============================================================


class LoaderError(Exception):
    """
    Base loader exception.
    """


class ModuleLoadError(LoaderError):
    """
    Raised when module loading fails.
    """


class DuplicateModuleError(LoaderError):
    """
    Raised on duplicate loading attempts.
    """


class CapabilityRegistrationError(LoaderError):
    """
    Raised when capability registration fails.
    """


# ============================================================
# LOAD CONTEXT
# ============================================================


@dataclass(frozen=True, slots=True)
class LoadContext:
    """
    Immutable module load context.

    Safe for:
    - async runtimes
    - distributed systems
    - plugin isolation
    """

    runtime_id: str
    environment: str = "production"

    permissions: FrozenSet[str] = frozenset()

    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )

    feature_flags: FrozenSet[str] = frozenset()

    timeout_seconds: float = 10.0


# ============================================================
# LOAD RESULT
# ============================================================


@dataclass(frozen=True, slots=True)
class LoadResult:
    """
    Immutable load result.
    """

    success: bool
    namespace: str
    loaded_at: float
    duration_ms: float

    capabilities: FrozenSet[Capability] = (
        frozenset()
    )

    error: Optional[str] = None


# ============================================================
# LOAD REGISTRY
# ============================================================


class LoadRegistry:
    """
    Institutional load registry.

    Tracks:
    - loaded modules
    - quarantined modules
    - failed modules
    - capability ownership
    - deterministic load state

    Does NOT own runtime state.
    """

    def __init__(self) -> None:

        self._loaded_modules: Dict[
            str,
            ModuleType,
        ] = {}

        self._manifests: Dict[
            str,
            ModuleManifest,
        ] = {}

        self._failed_modules: Dict[
            str,
            str,
        ] = {}

        self._quarantined_modules: Set[str] = set()

        self._capability_index: Dict[
            Capability,
            Set[str],
        ] = {}

    # ========================================================
    # REGISTRATION
    # ========================================================

    def register(
        self,
        *,
        namespace: str,
        module: ModuleType,
        manifest: ModuleManifest,
    ) -> None:
        """
        Register loaded module safely.
        """

        if namespace in self._loaded_modules:
            raise DuplicateModuleError(
                f"Module already loaded: {namespace}"
            )

        self._loaded_modules[namespace] = module

        self._manifests[namespace] = manifest

        for capability in manifest.capabilities:

            self._capability_index.setdefault(
                capability,
                set(),
            ).add(namespace)

    # ========================================================
    # FAILURE TRACKING
    # ========================================================

    def register_failure(
        self,
        namespace: str,
        reason: str,
    ) -> None:
        """
        Register deterministic failure.
        """

        self._failed_modules[namespace] = reason

    def quarantine(
        self,
        namespace: str,
    ) -> None:
        """
        Quarantine unsafe module.
        """

        self._quarantined_modules.add(namespace)

    # ========================================================
    # LOOKUPS
    # ========================================================

    def is_loaded(
        self,
        namespace: str,
    ) -> bool:
        return namespace in self._loaded_modules

    def is_quarantined(
        self,
        namespace: str,
    ) -> bool:
        return namespace in self._quarantined_modules

    def get_module(
        self,
        namespace: str,
    ) -> Optional[ModuleType]:
        return self._loaded_modules.get(namespace)

    def get_manifest(
        self,
        namespace: str,
    ) -> Optional[ModuleManifest]:
        return self._manifests.get(namespace)

    # ========================================================
    # SNAPSHOT
    # ========================================================

    def snapshot(self) -> Dict[str, Any]:
        """
        Immutable-safe registry snapshot.
        """

        return {
            "loaded_modules":
                tuple(
                    sorted(
                        self._loaded_modules.keys()
                    )
                ),
            "failed_modules":
                dict(self._failed_modules),
            "quarantined_modules":
                tuple(
                    sorted(
                        self._quarantined_modules
                    )
                ),
            "capabilities": {
                capability.value: tuple(
                    sorted(namespaces)
                )
                for capability, namespaces
                in self._capability_index.items()
            },
        }

    # ========================================================
    # CLEANUP
    # ========================================================

    def clear(self) -> None:
        """
        Cleanup registry state.
        """

        self._loaded_modules.clear()

        self._manifests.clear()

        self._failed_modules.clear()

        self._quarantined_modules.clear()

        self._capability_index.clear()


# ============================================================
# MODULE LOADER
# ============================================================


class ModuleLoader:
    """
    Institutional-grade async module loader.

    Responsibilities:
    - lazy imports
    - isolated loading
    - rollback-safe registration
    - deterministic loading
    - capability indexing
    - async-safe initialization

    Non-Responsibilities:
    - scanning
    - orchestration
    - lifecycle management
    - runtime ownership
    """

    def __init__(
        self,
        *,
        validator: Optional[
            ManifestValidator
        ] = None,
        registry: Optional[
            LoadRegistry
        ] = None,
        max_concurrency: int = 16,
    ) -> None:

        self._validator = (
            validator
            or ManifestValidator()
        )

        self._registry = (
            registry
            or LoadRegistry()
        )

        self._semaphore = asyncio.Semaphore(
            max(1, max_concurrency)
        )

        self._load_cache: Dict[
            str,
            LoadResult,
        ] = {}

    # ========================================================
    # PUBLIC API
    # ========================================================

    async def load(
        self,
        *,
        namespace: str,
        manifest: ModuleManifest,
        context: LoadContext,
    ) -> LoadResult:
        """
        Lazy-load module safely.

        Guarantees:
        - idempotent loading
        - deterministic results
        - rollback-safe registration
        - quarantine protection
        """

        start = time.perf_counter()

        # ====================================================
        # IDPOTENCY
        # ====================================================

        cached = self._load_cache.get(namespace)

        if cached:
            return cached

        if self._registry.is_quarantined(
            namespace
        ):
            return LoadResult(
                success=False,
                namespace=namespace,
                loaded_at=time.time(),
                duration_ms=0.0,
                error="Module quarantined.",
            )

        async with self._semaphore:

            try:

                # ============================================
                # VALIDATE MANIFEST
                # ============================================

                validation = (
                    self._validator.validate(
                        manifest
                    )
                )

                if not validation.valid:

                    raise ModuleLoadError(
                        "; ".join(
                            validation.errors
                        )
                    )

                # ============================================
                # DUPLICATE PREVENTION
                # ============================================

                if self._registry.is_loaded(
                    namespace
                ):
                    raise DuplicateModuleError(
                        f"Module already loaded: "
                        f"{namespace}"
                    )

                # ============================================
                # IMPORT MODULE
                # ============================================

                module = await asyncio.wait_for(
                    asyncio.to_thread(
                        importlib.import_module,
                        namespace,
                    ),
                    timeout=context.timeout_seconds,
                )

                # ============================================
                # SAFE REGISTRATION
                # ============================================

                self._registry.register(
                    namespace=namespace,
                    module=module,
                    manifest=manifest,
                )

                duration_ms = (
                    time.perf_counter()
                    - start
                ) * 1000.0

                result = LoadResult(
                    success=True,
                    namespace=namespace,
                    loaded_at=time.time(),
                    duration_ms=duration_ms,
                    capabilities=(
                        manifest.capabilities
                    ),
                )

                self._load_cache[
                    namespace
                ] = result

                logger.info(
                    "Module loaded | "
                    "namespace=%s duration_ms=%.2f",
                    namespace,
                    duration_ms,
                )

                return result

            except asyncio.TimeoutError:

                self._registry.quarantine(
                    namespace
                )

                self._registry.register_failure(
                    namespace,
                    "Load timeout.",
                )

                logger.exception(
                    "Module load timeout: %s",
                    namespace,
                )

                return LoadResult(
                    success=False,
                    namespace=namespace,
                    loaded_at=time.time(),
                    duration_ms=0.0,
                    error="Load timeout.",
                )

            except Exception as exc:

                self._rollback(namespace)

                self._registry.quarantine(
                    namespace
                )

                self._registry.register_failure(
                    namespace,
                    str(exc),
                )

                logger.exception(
                    "Module load failure: %s",
                    namespace,
                    exc_info=exc,
                )

                return LoadResult(
                    success=False,
                    namespace=namespace,
                    loaded_at=time.time(),
                    duration_ms=0.0,
                    error=str(exc),
                )

    # ========================================================
    # BULK LOAD
    # ========================================================

    async def load_many(
        self,
        *,
        modules: Mapping[
            str,
            ModuleManifest,
        ],
        context: LoadContext,
    ) -> Tuple[LoadResult, ...]:
        """
        Concurrent async-safe bulk loading.
        """

        tasks = [

            self.load(
                namespace=namespace,
                manifest=manifest,
                context=context,
            )

            for namespace, manifest
            in modules.items()
        ]

        return tuple(
            await asyncio.gather(*tasks)
        )

    # ========================================================
    # ROLLBACK
    # ========================================================

    def _rollback(
        self,
        namespace: str,
    ) -> None:
        """
        Rollback partial registration safely.
        """

        registry = self._registry

        registry._loaded_modules.pop(
            namespace,
            None,
        )

        registry._manifests.pop(
            namespace,
            None,
        )

        for namespaces in (
            registry._capability_index.values()
        ):
            namespaces.discard(namespace)

    # ========================================================
    # LOOKUPS
    # ========================================================

    def get_module(
        self,
        namespace: str,
    ) -> Optional[ModuleType]:
        """
        Retrieve loaded module.
        """

        return self._registry.get_module(
            namespace
        )

    def registry_snapshot(
        self,
    ) -> Dict[str, Any]:
        """
        Return registry snapshot.
        """

        return self._registry.snapshot()

    # ========================================================
    # CACHE CONTROL
    # ========================================================

    def clear_cache(self) -> None:
        """
        Clear deterministic load cache.
        """

        self._load_cache.clear()

    # ========================================================
    # CLEANUP
    # ========================================================

    async def shutdown(self) -> None:
        """
        Graceful loader shutdown.

        Keeps lifecycle ownership external.
        """

        self.clear_cache()

        self._registry.clear()

        logger.info(
            "ModuleLoader shutdown complete."
        )
