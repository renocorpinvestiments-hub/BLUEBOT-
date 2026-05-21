# core/plugins/registry.py
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import (
    Any,
    Dict,
    FrozenSet,
    Generic,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypeVar,
)


# =========================================================
# Exceptions
# =========================================================


class RegistryError(Exception):
    """Base registry exception."""


class DuplicateRegistrationError(RegistryError):
    """Raised when duplicate registrations are detected."""


class CompatibilityError(RegistryError):
    """Raised when compatibility validation fails."""


class ValidationError(RegistryError):
    """Raised when invalid metadata is registered."""


class NotRegisteredError(RegistryError):
    """Raised when a requested entity does not exist."""


# =========================================================
# Constants
# =========================================================


SCHEMA_VERSION = "1.0"


# =========================================================
# Immutable Models
# =========================================================


@dataclass(frozen=True, slots=True)
class PluginManifest:
    name: str
    version: str
    module: str
    description: str = ""
    author: str = ""
    schema_version: str = SCHEMA_VERSION
    capabilities: FrozenSet[str] = field(default_factory=frozenset)
    dependencies: FrozenSet[str] = field(default_factory=frozenset)
    optional_dependencies: FrozenSet[str] = field(default_factory=frozenset)
    tags: FrozenSet[str] = field(default_factory=frozenset)
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def identity(self) -> str:
        return f"{self.name}:{self.version}"

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "name": self.name,
                "version": self.version,
                "module": self.module,
                "capabilities": sorted(self.capabilities),
                "dependencies": sorted(self.dependencies),
            },
            sort_keys=True,
        )

        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ServiceRegistration:
    plugin: str
    service_name: str
    interface: str
    implementation: str
    version: str
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True, slots=True)
class EventRegistration:
    plugin: str
    event_name: str
    handler: str
    priority: int = 100


@dataclass(frozen=True, slots=True)
class RouteRegistration:
    plugin: str
    route: str
    handler: str
    methods: Tuple[str, ...] = field(default_factory=tuple)


# =========================================================
# Registry Index
# =========================================================


T = TypeVar("T")


class ImmutableIndex(Generic[T]):
    """
    Read-optimized append-safe registry index.

    Designed for:
    - fast lookups
    - deterministic iteration
    - low mutation frequency
    - high read concurrency
    """

    __slots__ = ("_store", "_lock")

    def __init__(self) -> None:
        self._store: Dict[str, T] = {}
        self._lock = threading.RLock()

    def register(self, key: str, value: T) -> None:
        with self._lock:
            existing = self._store.get(key)

            if existing == value:
                return

            if existing is not None:
                raise DuplicateRegistrationError(
                    f"Duplicate registration: {key}"
                )

            self._store[key] = value

    def get(self, key: str) -> T:
        try:
            return self._store[key]

        except KeyError as exc:
            raise NotRegisteredError(key) from exc

    def remove(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def contains(self, key: str) -> bool:
        return key in self._store

    def values(self) -> Tuple[T, ...]:
        return tuple(sorted(
            self._store.values(),
            key=lambda x: repr(x),
        ))

    def items(self) -> Tuple[Tuple[str, T], ...]:
        return tuple(sorted(self._store.items()))

    def snapshot(self) -> Mapping[str, T]:
        return MappingProxyType(dict(self._store))

    def __len__(self) -> int:
        return len(self._store)


# =========================================================
# Capability Registry
# =========================================================


class CapabilityRegistry:
    """
    Capability ownership registry.

    This file intentionally does NOT enforce permissions.
    security/ owns policy enforcement.
    """

    __slots__ = ("_capabilities",)

    def __init__(self) -> None:
        self._capabilities: Dict[str, FrozenSet[str]] = {}

    def register(
        self,
        plugin: str,
        capabilities: FrozenSet[str],
    ) -> None:
        existing = self._capabilities.get(plugin)

        if existing == capabilities:
            return

        if existing is not None:
            raise DuplicateRegistrationError(
                f"Capabilities already registered for '{plugin}'"
            )

        self._capabilities[plugin] = capabilities

    def get(self, plugin: str) -> FrozenSet[str]:
        return self._capabilities.get(plugin, frozenset())

    def plugins_with(self, capability: str) -> Tuple[str, ...]:
        return tuple(sorted(
            plugin
            for plugin, capabilities in self._capabilities.items()
            if capability in capabilities
        ))


# =========================================================
# Dependency Index
# =========================================================


class DependencyMap:
    """
    Fast immutable dependency graph.

    Runtime execution belongs to manager.py.
    """

    __slots__ = (
        "_forward",
        "_reverse",
    )

    def __init__(self) -> None:
        self._forward: Dict[str, FrozenSet[str]] = {}
        self._reverse: Dict[str, Set[str]] = defaultdict(set)

    def register(
        self,
        plugin: str,
        dependencies: FrozenSet[str],
    ) -> None:
        existing = self._forward.get(plugin)

        if existing == dependencies:
            return

        if existing is not None:
            raise DuplicateRegistrationError(
                f"Dependencies already registered for '{plugin}'"
            )

        self._forward[plugin] = dependencies

        for dependency in dependencies:
            self._reverse[dependency].add(plugin)

    def dependencies_of(self, plugin: str) -> FrozenSet[str]:
        return self._forward.get(plugin, frozenset())

    def dependents_of(self, plugin: str) -> Tuple[str, ...]:
        return tuple(sorted(self._reverse.get(plugin, set())))


# =========================================================
# Interface Registry
# =========================================================


class InterfaceRegistry:
    """
    Interface-to-service mappings.

    Prevents direct coupling between plugins.
    """

    __slots__ = ("_interfaces",)

    def __init__(self) -> None:
        self._interfaces: Dict[str, Set[str]] = defaultdict(set)

    def register(
        self,
        interface: str,
        service_name: str,
    ) -> None:
        self._interfaces[interface].add(service_name)

    def implementations(
        self,
        interface: str,
    ) -> Tuple[str, ...]:
        return tuple(sorted(self._interfaces.get(interface, set())))


# =========================================================
# Plugin Registry
# =========================================================


class PluginRegistry:
    """
    Institutional-grade metadata registry.

    Responsibilities:
    - plugin metadata registration
    - service indexing
    - capability indexing
    - route registration
    - event registration
    - dependency indexing
    - compatibility validation
    - immutable snapshots

    Explicitly NOT responsible for:
    - plugin lifecycle
    - runtime execution
    - sandboxing
    - event dispatch
    - routing execution
    - security enforcement
    - discovery scanning
    - observability
    """

    __slots__ = (
        "_initialized",
        "_lock",
        "_manifests",
        "_services",
        "_events",
        "_routes",
        "_capabilities",
        "_dependencies",
        "_interfaces",
    )

    def __init__(self) -> None:
        self._initialized = False
        self._lock = asyncio.Lock()

        self._manifests = ImmutableIndex[PluginManifest]()
        self._services = ImmutableIndex[ServiceRegistration]()
        self._events = ImmutableIndex[EventRegistration]()
        self._routes = ImmutableIndex[RouteRegistration]()

        self._capabilities = CapabilityRegistry()
        self._dependencies = DependencyMap()
        self._interfaces = InterfaceRegistry()

    # =====================================================
    # Lifecycle
    # =====================================================

    async def initialize(self) -> None:
        """
        Idempotent async initialization.
        """

        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            self._initialized = True

    # =====================================================
    # Plugin Registration
    # =====================================================

    async def register_plugin(
        self,
        manifest: PluginManifest,
    ) -> None:
        await self.initialize()

        self._validate_manifest(manifest)
        self._validate_compatibility(manifest)

        self._manifests.register(
            manifest.name,
            manifest,
        )

        self._capabilities.register(
            manifest.name,
            manifest.capabilities,
        )

        self._dependencies.register(
            manifest.name,
            manifest.dependencies,
        )

    async def unregister_plugin(
        self,
        plugin_name: str,
    ) -> None:
        self._manifests.remove(plugin_name)

    # =====================================================
    # Service Registration
    # =====================================================

    async def register_service(
        self,
        registration: ServiceRegistration,
    ) -> None:
        self._ensure_plugin_exists(registration.plugin)

        key = (
            f"{registration.plugin}:"
            f"{registration.service_name}:"
            f"{registration.version}"
        )

        self._services.register(key, registration)

        self._interfaces.register(
            registration.interface,
            registration.service_name,
        )

    async def register_event(
        self,
        registration: EventRegistration,
    ) -> None:
        self._ensure_plugin_exists(registration.plugin)

        key = (
            f"{registration.plugin}:"
            f"{registration.event_name}:"
            f"{registration.handler}"
        )

        self._events.register(key, registration)

    async def register_route(
        self,
        registration: RouteRegistration,
    ) -> None:
        self._ensure_plugin_exists(registration.plugin)

        key = (
            f"{registration.plugin}:"
            f"{registration.route}:"
            f"{registration.handler}"
        )

        self._routes.register(key, registration)

    # =====================================================
    # Lookup APIs
    # =====================================================

    def plugin(
        self,
        plugin_name: str,
    ) -> PluginManifest:
        return self._manifests.get(plugin_name)

    def plugins(self) -> Tuple[PluginManifest, ...]:
        return self._manifests.values()

    def services(self) -> Tuple[ServiceRegistration, ...]:
        return self._services.values()

    def events(self) -> Tuple[EventRegistration, ...]:
        return self._events.values()

    def routes(self) -> Tuple[RouteRegistration, ...]:
        return self._routes.values()

    def capabilities_of(
        self,
        plugin_name: str,
    ) -> FrozenSet[str]:
        return self._capabilities.get(plugin_name)

    def dependencies_of(
        self,
        plugin_name: str,
    ) -> FrozenSet[str]:
        return self._dependencies.dependencies_of(plugin_name)

    def dependents_of(
        self,
        plugin_name: str,
    ) -> Tuple[str, ...]:
        return self._dependencies.dependents_of(plugin_name)

    def implementations_of(
        self,
        interface: str,
    ) -> Tuple[str, ...]:
        return self._interfaces.implementations(interface)

    # =====================================================
    # Snapshots
    # =====================================================

    def snapshot(self) -> Mapping[str, Any]:
        """
        Immutable registry snapshot.

        Useful for:
        - observability exports
        - diagnostics
        - distributed synchronization
        - audit systems

        Without exposing mutable state.
        """

        return MappingProxyType(
            {
                "schema_version": SCHEMA_VERSION,
                "plugins": tuple(
                    asdict(plugin)
                    for plugin in self.plugins()
                ),
                "services": tuple(
                    asdict(service)
                    for service in self.services()
                ),
                "events": tuple(
                    asdict(event)
                    for event in self.events()
                ),
                "routes": tuple(
                    asdict(route)
                    for route in self.routes()
                ),
            }
        )

    # =====================================================
    # Validation
    # =====================================================

    def _validate_manifest(
        self,
        manifest: PluginManifest,
    ) -> None:
        if not manifest.name:
            raise ValidationError("Plugin name is required")

        if not manifest.version:
            raise ValidationError("Plugin version is required")

        if not manifest.module:
            raise ValidationError("Plugin module is required")

        if manifest.schema_version != SCHEMA_VERSION:
            raise CompatibilityError(
                "Unsupported schema version"
            )

    def _validate_compatibility(
        self,
        manifest: PluginManifest,
    ) -> None:
        for dependency in manifest.dependencies:
            if not self._manifests.contains(dependency):
                raise CompatibilityError(
                    f"Missing dependency '{dependency}'"
                )

    def _ensure_plugin_exists(
        self,
        plugin_name: str,
    ) -> None:
        if not self._manifests.contains(plugin_name):
            raise NotRegisteredError(
                f"Plugin '{plugin_name}' is not registered"
            )


# =========================================================
# Utilities
# =========================================================


def build_manifest(
    *,
    name: str,
    version: str,
    module: str,
    description: str = "",
    author: str = "",
    capabilities: Optional[Iterable[str]] = None,
    dependencies: Optional[Iterable[str]] = None,
    optional_dependencies: Optional[Iterable[str]] = None,
    tags: Optional[Iterable[str]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> PluginManifest:
    """
    Stable manifest builder.

    Prevents external systems from coupling directly
    to registry internals.
    """

    return PluginManifest(
        name=name,
        version=version,
        module=module,
        description=description,
        author=author,
        capabilities=frozenset(capabilities or ()),
        dependencies=frozenset(dependencies or ()),
        optional_dependencies=frozenset(
            optional_dependencies or ()
        ),
        tags=frozenset(tags or ()),
        metadata=MappingProxyType(dict(metadata or {})),
    )


__all__ = [
    "RegistryError",
    "DuplicateRegistrationError",
    "CompatibilityError",
    "ValidationError",
    "NotRegisteredError",
    "PluginManifest",
    "ServiceRegistration",
    "EventRegistration",
    "RouteRegistration",
    "CapabilityRegistry",
    "DependencyMap",
    "InterfaceRegistry",
    "PluginRegistry",
    "build_manifest",
]
