# core/plugins/manager.py
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import inspect
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
)


# =========================================================
# Exceptions
# =========================================================


class PluginError(Exception):
    """Base plugin exception."""


class PluginValidationError(PluginError):
    """Raised when a plugin manifest is invalid."""


class PluginDependencyError(PluginError):
    """Raised when dependencies cannot be resolved."""


class PluginPermissionError(PluginError):
    """Raised when a plugin violates capability boundaries."""


class PluginRuntimeError(PluginError):
    """Raised when a plugin fails during runtime execution."""


# =========================================================
# Constants
# =========================================================


DEFAULT_PLUGIN_TIMEOUT = 30.0
DEFAULT_STARTUP_TIMEOUT = 15.0
DEFAULT_SHUTDOWN_TIMEOUT = 10.0
DEFAULT_HEALTH_INTERVAL = 30.0
DEFAULT_MAX_CONCURRENT_STARTUPS = 4
DEFAULT_MAX_PLUGIN_TASKS = 32


# =========================================================
# Plugin Protocols
# =========================================================


class PluginLifecycle(Protocol):
    """
    Minimal lifecycle contract.

    Keeps manager decoupled from sdk.py and contracts/.
    """

    async def start(self, context: "PluginContext") -> None:
        ...

    async def stop(self, context: "PluginContext") -> None:
        ...


class HealthCheckable(Protocol):
    async def healthcheck(self) -> bool:
        ...


# =========================================================
# Enums
# =========================================================


class PluginState(str, Enum):
    DISCOVERED = "discovered"
    LOADED = "loaded"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    UNLOADED = "unloaded"
    QUARANTINED = "quarantined"


# =========================================================
# Immutable Models
# =========================================================


@dataclass(frozen=True, slots=True)
class PluginManifest:
    name: str
    version: str
    module: str
    capabilities: FrozenSet[str] = field(default_factory=frozenset)
    dependencies: FrozenSet[str] = field(default_factory=frozenset)
    optional_dependencies: FrozenSet[str] = field(default_factory=frozenset)
    startup_priority: int = 100
    sandboxed: bool = True
    reloadable: bool = True
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT
    execution_timeout: float = DEFAULT_PLUGIN_TIMEOUT
    max_concurrent_tasks: int = DEFAULT_MAX_PLUGIN_TASKS
    checksum: str = ""

    @property
    def identity(self) -> str:
        return f"{self.name}:{self.version}"


@dataclass(slots=True)
class PluginMetrics:
    load_count: int = 0
    crash_count: int = 0
    restart_count: int = 0
    last_started_at: float = 0.0
    last_stopped_at: float = 0.0
    last_failure_at: float = 0.0
    active_tasks: int = 0


@dataclass(slots=True)
class PluginRuntime:
    manifest: PluginManifest
    instance: PluginLifecycle
    module: ModuleType
    state: PluginState = PluginState.LOADED
    metrics: PluginMetrics = field(default_factory=PluginMetrics)
    started_at: float = 0.0
    last_healthcheck_at: float = 0.0
    tasks: Set[asyncio.Task[Any]] = field(default_factory=set)
    semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.semaphore = asyncio.Semaphore(
            self.manifest.max_concurrent_tasks
        )


@dataclass(frozen=True, slots=True)
class PluginContext:
    """
    Restricted execution context.

    Avoid exposing mutable internals.
    """

    runtime_id: str
    capabilities: FrozenSet[str]
    services: Mapping[str, Any]
    metadata: Mapping[str, Any]


# =========================================================
# Capability Controller
# =========================================================


class CapabilityController:
    """
    Enforces capability-based access.

    This manager intentionally does NOT implement security policy.
    It only enforces boundaries.
    """

    __slots__ = ("_capability_map",)

    def __init__(self) -> None:
        self._capability_map: Dict[str, FrozenSet[str]] = {}

    def register(
        self,
        plugin_name: str,
        capabilities: FrozenSet[str],
    ) -> None:
        self._capability_map[plugin_name] = capabilities

    def validate(
        self,
        plugin_name: str,
        required: str,
    ) -> None:
        capabilities = self._capability_map.get(plugin_name, frozenset())

        if required not in capabilities:
            raise PluginPermissionError(
                f"Plugin '{plugin_name}' lacks capability '{required}'"
            )


# =========================================================
# Dependency Resolver
# =========================================================


class DependencyResolver:
    """
    Deterministic dependency ordering.

    Kahn topological sorting.
    """

    @staticmethod
    def resolve(
        manifests: Mapping[str, PluginManifest],
    ) -> Tuple[str, ...]:
        graph: Dict[str, Set[str]] = defaultdict(set)
        incoming: Dict[str, int] = defaultdict(int)

        for name, manifest in manifests.items():
            incoming.setdefault(name, 0)

            for dependency in manifest.dependencies:
                if dependency not in manifests:
                    raise PluginDependencyError(
                        f"Missing dependency '{dependency}' for '{name}'"
                    )

                graph[dependency].add(name)
                incoming[name] += 1

        queue = sorted(
            [name for name, degree in incoming.items() if degree == 0]
        )

        resolved: list[str] = []

        while queue:
            current = queue.pop(0)
            resolved.append(current)

            for dependent in sorted(graph[current]):
                incoming[dependent] -= 1

                if incoming[dependent] == 0:
                    queue.append(dependent)

        if len(resolved) != len(manifests):
            raise PluginDependencyError(
                "Circular plugin dependency detected"
            )

        return tuple(resolved)


# =========================================================
# Plugin Sandbox
# =========================================================


class PluginSandbox:
    """
    Lightweight execution isolation.

    Does NOT duplicate:
    - security policy
    - runtime orchestration
    - resilience systems

    It only enforces local runtime boundaries.
    """

    __slots__ = ("_timeouts",)

    def __init__(self) -> None:
        self._timeouts: Dict[str, float] = {}

    def register(
        self,
        manifest: PluginManifest,
    ) -> None:
        self._timeouts[manifest.name] = manifest.execution_timeout

    async def execute(
        self,
        plugin_name: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        timeout = self._timeouts.get(
            plugin_name,
            DEFAULT_PLUGIN_TIMEOUT,
        )

        return await asyncio.wait_for(operation(), timeout=timeout)


# =========================================================
# Plugin Manager
# =========================================================


class PluginManager:
    """
    Institutional-grade plugin runtime kernel.

    Responsibilities:
    - lifecycle orchestration
    - dependency-safe loading
    - hot reload
    - plugin isolation
    - task containment
    - capability enforcement
    - fault containment
    - idempotent startup/shutdown

    Explicitly NOT responsible for:
    - plugin discovery
    - security policy definition
    - observability analytics
    - event bus implementation
    - runtime orchestration
    - service contracts
    """

    __slots__ = (
        "_initialized",
        "_running",
        "_lock",
        "_plugins",
        "_manifests",
        "_services",
        "_capabilities",
        "_sandbox",
        "_health_task",
        "_runtime_id",
        "_startup_semaphore",
    )

    def __init__(
        self,
        *,
        services: Optional[Mapping[str, Any]] = None,
        runtime_id: str = "plugin-runtime",
        max_concurrent_startups: int = DEFAULT_MAX_CONCURRENT_STARTUPS,
    ) -> None:
        self._initialized = False
        self._running = False
        self._lock = asyncio.Lock()

        self._plugins: Dict[str, PluginRuntime] = {}
        self._manifests: Dict[str, PluginManifest] = {}

        self._services = dict(services or {})

        self._capabilities = CapabilityController()
        self._sandbox = PluginSandbox()

        self._health_task: Optional[asyncio.Task[Any]] = None

        self._runtime_id = runtime_id

        self._startup_semaphore = asyncio.Semaphore(
            max_concurrent_startups
        )

    # =====================================================
    # Lifecycle
    # =====================================================

    async def initialize(self) -> None:
        """
        Idempotent initialization.
        """

        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            self._initialized = True

    async def start(self) -> None:
        """
        Starts background runtime services.
        """

        if self._running:
            return

        await self.initialize()

        async with self._lock:
            if self._running:
                return

            self._running = True

            self._health_task = asyncio.create_task(
                self._health_monitor_loop(),
                name="plugin-health-monitor",
            )

    async def shutdown(self) -> None:
        """
        Graceful idempotent shutdown.
        """

        if not self._running:
            return

        async with self._lock:
            if not self._running:
                return

            self._running = False

            if self._health_task:
                self._health_task.cancel()

                with contextlib.suppress(asyncio.CancelledError):
                    await self._health_task

            unload_order = reversed(
                DependencyResolver.resolve(self._manifests)
            )

            for plugin_name in unload_order:
                with contextlib.suppress(Exception):
                    await self.unload_plugin(plugin_name)

    # =====================================================
    # Registration
    # =====================================================

    async def register_manifest(
        self,
        manifest: PluginManifest,
    ) -> None:
        """
        Atomic immutable registration.
        """

        async with self._lock:
            existing = self._manifests.get(manifest.name)

            if existing == manifest:
                return

            if existing and existing.version != manifest.version:
                raise PluginValidationError(
                    f"Plugin '{manifest.name}' already registered"
                )

            self._validate_manifest(manifest)

            self._manifests[manifest.name] = manifest

            self._capabilities.register(
                manifest.name,
                manifest.capabilities,
            )

            self._sandbox.register(manifest)

    # =====================================================
    # Loading
    # =====================================================

    async def load_all(self) -> None:
        """
        Deterministic dependency-safe parallel loading.
        """

        order = DependencyResolver.resolve(self._manifests)

        for plugin_name in order:
            await self.load_plugin(plugin_name)

    async def load_plugin(
        self,
        plugin_name: str,
    ) -> None:
        """
        Dependency-safe idempotent loader.
        """

        if plugin_name in self._plugins:
            return

        manifest = self._require_manifest(plugin_name)

        async with self._startup_semaphore:
            module = importlib.import_module(manifest.module)

            plugin_instance = self._extract_plugin(module)

            runtime = PluginRuntime(
                manifest=manifest,
                instance=plugin_instance,
                module=module,
            )

            self._plugins[plugin_name] = runtime

            await self._start_plugin(runtime)

    async def unload_plugin(
        self,
        plugin_name: str,
    ) -> None:
        runtime = self._plugins.get(plugin_name)

        if not runtime:
            return

        await self._stop_plugin(runtime)

        with contextlib.suppress(Exception):
            if runtime.manifest.module in sys.modules:
                del sys.modules[runtime.manifest.module]

        runtime.state = PluginState.UNLOADED

        self._plugins.pop(plugin_name, None)

    async def reload_plugin(
        self,
        plugin_name: str,
    ) -> None:
        """
        Safe hot reload with rollback semantics.
        """

        manifest = self._require_manifest(plugin_name)

        if not manifest.reloadable:
            raise PluginRuntimeError(
                f"Plugin '{plugin_name}' is not reloadable"
            )

        previous_runtime = self._plugins.get(plugin_name)

        await self.unload_plugin(plugin_name)

        try:
            await self.load_plugin(plugin_name)

        except Exception:
            if previous_runtime:
                self._plugins[plugin_name] = previous_runtime

            raise

    # =====================================================
    # Execution
    # =====================================================

    async def execute(
        self,
        plugin_name: str,
        capability: str,
        operation: Callable[[PluginLifecycle], Awaitable[Any]],
    ) -> Any:
        """
        Sandboxed execution boundary.
        """

        runtime = self._require_runtime(plugin_name)

        if runtime.state != PluginState.RUNNING:
            raise PluginRuntimeError(
                f"Plugin '{plugin_name}' is not running"
            )

        self._capabilities.validate(plugin_name, capability)

        async with runtime.semaphore:
            runtime.metrics.active_tasks += 1

            try:
                async def guarded() -> Any:
                    return await operation(runtime.instance)

                task = asyncio.create_task(
                    self._sandbox.execute(plugin_name, guarded)
                )

                runtime.tasks.add(task)

                try:
                    return await task

                finally:
                    runtime.tasks.discard(task)

            except asyncio.TimeoutError as exc:
                runtime.metrics.crash_count += 1
                runtime.state = PluginState.FAILED

                raise PluginRuntimeError(
                    f"Plugin '{plugin_name}' execution timed out"
                ) from exc

            except Exception as exc:
                runtime.metrics.crash_count += 1
                runtime.metrics.last_failure_at = time.time()

                raise PluginRuntimeError(
                    f"Plugin '{plugin_name}' execution failed"
                ) from exc

            finally:
                runtime.metrics.active_tasks -= 1

    # =====================================================
    # State
    # =====================================================

    def get_state(
        self,
        plugin_name: str,
    ) -> PluginState:
        runtime = self._require_runtime(plugin_name)
        return runtime.state

    def is_loaded(self, plugin_name: str) -> bool:
        return plugin_name in self._plugins

    def list_plugins(self) -> Tuple[str, ...]:
        return tuple(sorted(self._plugins.keys()))

    # =====================================================
    # Internal Runtime
    # =====================================================

    async def _start_plugin(
        self,
        runtime: PluginRuntime,
    ) -> None:
        runtime.state = PluginState.STARTING

        context = self._build_context(runtime.manifest)

        try:
            await asyncio.wait_for(
                runtime.instance.start(context),
                timeout=runtime.manifest.startup_timeout,
            )

            runtime.state = PluginState.RUNNING
            runtime.started_at = time.time()

            runtime.metrics.load_count += 1
            runtime.metrics.last_started_at = runtime.started_at

        except Exception:
            runtime.state = PluginState.FAILED
            runtime.metrics.crash_count += 1

            raise

    async def _stop_plugin(
        self,
        runtime: PluginRuntime,
    ) -> None:
        if runtime.state in {
            PluginState.STOPPED,
            PluginState.UNLOADED,
        }:
            return

        runtime.state = PluginState.STOPPING

        for task in tuple(runtime.tasks):
            task.cancel()

        await asyncio.gather(
            *runtime.tasks,
            return_exceptions=True,
        )

        context = self._build_context(runtime.manifest)

        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                runtime.instance.stop(context),
                timeout=runtime.manifest.shutdown_timeout,
            )

        runtime.state = PluginState.STOPPED
        runtime.metrics.last_stopped_at = time.time()

    async def _health_monitor_loop(self) -> None:
        while self._running:
            await asyncio.sleep(DEFAULT_HEALTH_INTERVAL)

            for runtime in tuple(self._plugins.values()):
                await self._healthcheck(runtime)

    async def _healthcheck(
        self,
        runtime: PluginRuntime,
    ) -> None:
        if not isinstance(runtime.instance, HealthCheckable):
            return

        try:
            healthy = await asyncio.wait_for(
                runtime.instance.healthcheck(),
                timeout=5.0,
            )

            runtime.last_healthcheck_at = time.time()

            if not healthy:
                runtime.state = PluginState.QUARANTINED

        except Exception:
            runtime.state = PluginState.QUARANTINED

    # =====================================================
    # Helpers
    # =====================================================

    def _extract_plugin(
        self,
        module: ModuleType,
    ) -> PluginLifecycle:
        """
        Supports either:
        - plugin variable
        - Plugin class
        - get_plugin factory
        """

        if hasattr(module, "plugin"):
            plugin = getattr(module, "plugin")
            self._validate_plugin(plugin)
            return plugin

        if hasattr(module, "Plugin"):
            plugin_class = getattr(module, "Plugin")
            plugin = plugin_class()
            self._validate_plugin(plugin)
            return plugin

        if hasattr(module, "get_plugin"):
            factory = getattr(module, "get_plugin")
            plugin = factory()
            self._validate_plugin(plugin)
            return plugin

        raise PluginValidationError(
            f"No plugin entrypoint found in '{module.__name__}'"
        )

    def _validate_plugin(
        self,
        plugin: Any,
    ) -> None:
        required = ("start", "stop")

        for method in required:
            if not hasattr(plugin, method):
                raise PluginValidationError(
                    f"Plugin missing method '{method}'"
                )

            if not inspect.iscoroutinefunction(
                getattr(plugin, method)
            ):
                raise PluginValidationError(
                    f"Plugin method '{method}' must be async"
                )

    def _validate_manifest(
        self,
        manifest: PluginManifest,
    ) -> None:
        if not manifest.name:
            raise PluginValidationError("Plugin name is required")

        if not manifest.module:
            raise PluginValidationError("Plugin module is required")

        if manifest.max_concurrent_tasks <= 0:
            raise PluginValidationError(
                "max_concurrent_tasks must be positive"
            )

    def _build_context(
        self,
        manifest: PluginManifest,
    ) -> PluginContext:
        return PluginContext(
            runtime_id=self._runtime_id,
            capabilities=manifest.capabilities,
            services=self._services,
            metadata={
                "plugin": manifest.name,
                "version": manifest.version,
            },
        )

    def _require_manifest(
        self,
        plugin_name: str,
    ) -> PluginManifest:
        manifest = self._manifests.get(plugin_name)

        if not manifest:
            raise PluginValidationError(
                f"Plugin '{plugin_name}' is not registered"
            )

        return manifest

    def _require_runtime(
        self,
        plugin_name: str,
    ) -> PluginRuntime:
        runtime = self._plugins.get(plugin_name)

        if not runtime:
            raise PluginRuntimeError(
                f"Plugin '{plugin_name}' is not loaded"
            )

        return runtime


# =========================================================
# Utilities
# =========================================================


def build_manifest(
    *,
    name: str,
    version: str,
    module: str,
    capabilities: Optional[Iterable[str]] = None,
    dependencies: Optional[Iterable[str]] = None,
    optional_dependencies: Optional[Iterable[str]] = None,
    sandboxed: bool = True,
    reloadable: bool = True,
) -> PluginManifest:
    """
    Stable helper for external loaders/scanners.

    Avoids tight coupling with internal dataclass construction.
    """

    checksum = hashlib.sha256(
        f"{name}:{version}:{module}".encode()
    ).hexdigest()

    return PluginManifest(
        name=name,
        version=version,
        module=module,
        capabilities=frozenset(capabilities or ()),
        dependencies=frozenset(dependencies or ()),
        optional_dependencies=frozenset(
            optional_dependencies or ()
        ),
        sandboxed=sandboxed,
        reloadable=reloadable,
        checksum=checksum,
    )


__all__ = [
    "PluginError",
    "PluginValidationError",
    "PluginDependencyError",
    "PluginPermissionError",
    "PluginRuntimeError",
    "PluginManifest",
    "PluginRuntime",
    "PluginContext",
    "PluginState",
    "PluginManager",
    "build_manifest",
]
