# core/plugins/sdk.py
from __future__ import annotations

import asyncio
import inspect
import warnings
from abc import ABC
from dataclasses import dataclass, field
from functools import wraps
from types import MappingProxyType
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    FrozenSet,
    Generic,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    Type,
    TypeVar,
    runtime_checkable,
)


# =========================================================
# SDK VERSIONING
# =========================================================


SDK_VERSION = "1.0.0"
SDK_API_VERSION = "1"


# =========================================================
# Exceptions
# =========================================================


class SDKError(Exception):
    """Base SDK exception."""


class CapabilityError(SDKError):
    """Raised when capability rules are violated."""


class HookValidationError(SDKError):
    """Raised when hook registration is invalid."""


class CompatibilityError(SDKError):
    """Raised when plugin compatibility fails."""


class ContractViolationError(SDKError):
    """Raised when SDK contracts are violated."""


# =========================================================
# Immutable SDK MODELS
# =========================================================


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    name: str
    version: str
    author: str = ""
    description: str = ""
    sdk_version: str = SDK_VERSION
    api_version: str = SDK_API_VERSION
    capabilities: FrozenSet[str] = field(default_factory=frozenset)
    dependencies: FrozenSet[str] = field(default_factory=frozenset)
    optional_dependencies: FrozenSet[str] = field(default_factory=frozenset)
    tags: FrozenSet[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class PluginContext:
    """
    Restricted immutable execution context.

    Prevents direct runtime mutation.
    """

    plugin_name: str
    runtime_id: str
    capabilities: FrozenSet[str]
    services: Mapping[str, Any]
    metadata: Mapping[str, Any]

    def require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise CapabilityError(
                f"Missing capability: {capability}"
            )


@dataclass(frozen=True, slots=True)
class HookDefinition:
    hook_type: str
    name: str
    priority: int
    async_only: bool


# =========================================================
# Protocols
# =========================================================


@runtime_checkable
class LifecycleProtocol(Protocol):
    async def start(self, context: PluginContext) -> None:
        ...

    async def stop(self, context: PluginContext) -> None:
        ...


@runtime_checkable
class HealthcheckProtocol(Protocol):
    async def healthcheck(self) -> bool:
        ...


# =========================================================
# Internal Registries
# =========================================================


_PLUGIN_REGISTRY: Dict[str, Type["PluginBase"]] = {}
_PLUGIN_METADATA: Dict[str, PluginMetadata] = {}


# =========================================================
# Hook Metadata
# =========================================================


HOOK_ATTRIBUTE = "__plugin_hook__"
CAPABILITY_ATTRIBUTE = "__plugin_capabilities__"


# =========================================================
# Base Plugin
# =========================================================


class PluginBase(ABC):
    """
    Stable plugin abstraction layer.

    This class intentionally avoids:
    - runtime orchestration
    - registry management
    - event dispatching
    - security ownership
    - service resolution logic
    - observability ownership

    It only defines safe extension contracts.
    """

    metadata: ClassVar[PluginMetadata]

    def __init__(self) -> None:
        self._context: Optional[PluginContext] = None
        self._started = False
        self._lock = asyncio.Lock()

    @property
    def context(self) -> PluginContext:
        if self._context is None:
            raise ContractViolationError(
                "Plugin context is unavailable"
            )

        return self._context

    async def start(self, context: PluginContext) -> None:
        """
        Idempotent lifecycle entrypoint.
        """

        if self._started:
            return

        async with self._lock:
            if self._started:
                return

            self._context = context

            await self.on_start()

            self._started = True

    async def stop(self, context: PluginContext) -> None:
        """
        Idempotent shutdown entrypoint.
        """

        if not self._started:
            return

        async with self._lock:
            if not self._started:
                return

            await self.on_stop()

            self._started = False

    async def on_start(self) -> None:
        """
        Override-safe startup hook.
        """

    async def on_stop(self) -> None:
        """
        Override-safe shutdown hook.
        """

    async def healthcheck(self) -> bool:
        """
        Override-safe healthcheck.
        """

        return True


# =========================================================
# Decorators
# =========================================================


P = TypeVar("P")


def register_plugin(
    *,
    name: str,
    version: str,
    author: str = "",
    description: str = "",
    capabilities: Optional[Iterable[str]] = None,
    dependencies: Optional[Iterable[str]] = None,
    optional_dependencies: Optional[Iterable[str]] = None,
    tags: Optional[Iterable[str]] = None,
) -> Callable[[Type[P]], Type[P]]:
    """
    Safe plugin registration decorator.

    Prevents plugins from mutating registry internals.
    """

    metadata = PluginMetadata(
        name=name,
        version=version,
        author=author,
        description=description,
        capabilities=frozenset(capabilities or ()),
        dependencies=frozenset(dependencies or ()),
        optional_dependencies=frozenset(
            optional_dependencies or ()
        ),
        tags=frozenset(tags or ()),
    )

    def decorator(cls: Type[P]) -> Type[P]:
        if not inspect.isclass(cls):
            raise ContractViolationError(
                "register_plugin requires a class"
            )

        if not issubclass(cls, PluginBase):
            raise ContractViolationError(
                "Plugins must inherit PluginBase"
            )

        existing = _PLUGIN_REGISTRY.get(name)

        if existing is cls:
            return cls

        if existing is not None:
            raise ContractViolationError(
                f"Plugin '{name}' already registered"
            )

        cls.metadata = metadata

        _PLUGIN_REGISTRY[name] = cls
        _PLUGIN_METADATA[name] = metadata

        return cls

    return decorator


# =========================================================
# Hook Decorators
# =========================================================


def _validate_hook(function: Callable[..., Any]) -> None:
    if not inspect.iscoroutinefunction(function):
        raise HookValidationError(
            "Plugin hooks must be async"
        )



def hook(
    *,
    hook_type: str,
    name: Optional[str] = None,
    priority: int = 100,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """
    Generic stable hook decorator.

    Does NOT execute hooks.
    Only defines metadata contracts.
    """

    def decorator(
        function: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        _validate_hook(function)

        definition = HookDefinition(
            hook_type=hook_type,
            name=name or function.__name__,
            priority=priority,
            async_only=True,
        )

        setattr(function, HOOK_ATTRIBUTE, definition)

        return function

    return decorator



def event_hook(
    event_name: str,
    *,
    priority: int = 100,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    return hook(
        hook_type="event",
        name=event_name,
        priority=priority,
    )



def service_hook(
    service_name: str,
    *,
    priority: int = 100,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    return hook(
        hook_type="service",
        name=service_name,
        priority=priority,
    )



def route_hook(
    route: str,
    *,
    priority: int = 100,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    return hook(
        hook_type="route",
        name=route,
        priority=priority,
    )


# =========================================================
# Capability Decorator
# =========================================================



def capability(
    *required_capabilities: str,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """
    Capability declaration wrapper.

    Declares requirements without enforcing runtime policy.
    """

    required = frozenset(required_capabilities)

    def decorator(
        function: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        _validate_hook(function)

        setattr(
            function,
            CAPABILITY_ATTRIBUTE,
            required,
        )

        @wraps(function)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            plugin_instance = args[0]

            if not isinstance(plugin_instance, PluginBase):
                raise CapabilityError(
                    "Capability decorators require PluginBase"
                )

            context = plugin_instance.context

            for capability_name in required:
                context.require(capability_name)

            return await function(*args, **kwargs)

        return wrapper

    return decorator


# =========================================================
# Compatibility
# =========================================================


class CompatibilityLayer:
    """
    SDK compatibility abstraction.

    Prevents future SDK evolution from breaking plugins.
    """

    __slots__ = ()

    @staticmethod
    def validate(metadata: PluginMetadata) -> None:
        if metadata.api_version != SDK_API_VERSION:
            raise CompatibilityError(
                "Incompatible SDK API version"
            )

    @staticmethod
    def deprecated(
        replacement: Optional[str] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(
            function: Callable[..., Any],
        ) -> Callable[..., Any]:
            @wraps(function)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                message = (
                    f"'{function.__name__}' is deprecated"
                )

                if replacement:
                    message += f"; use '{replacement}' instead"

                warnings.warn(
                    message,
                    category=DeprecationWarning,
                    stacklevel=2,
                )

                return function(*args, **kwargs)

            return wrapper

        return decorator


# =========================================================
# SDK Inspection APIs
# =========================================================



def get_registered_plugins() -> Tuple[str, ...]:
    return tuple(sorted(_PLUGIN_REGISTRY.keys()))



def get_plugin_class(
    plugin_name: str,
) -> Type[PluginBase]:
    try:
        return _PLUGIN_REGISTRY[plugin_name]

    except KeyError as exc:
        raise SDKError(
            f"Plugin '{plugin_name}' is not registered"
        ) from exc



def get_plugin_metadata(
    plugin_name: str,
) -> PluginMetadata:
    try:
        return _PLUGIN_METADATA[plugin_name]

    except KeyError as exc:
        raise SDKError(
            f"Plugin metadata '{plugin_name}' not found"
        ) from exc



def get_hooks(
    plugin: PluginBase,
) -> Mapping[str, Tuple[Callable[..., Any], ...]]:
    """
    Immutable hook inspection API.

    Used by registry/manager without exposing SDK internals.
    """

    hooks: Dict[str, list[Callable[..., Any]]] = {}

    for attribute_name in dir(plugin):
        attribute = getattr(plugin, attribute_name)

        definition = getattr(
            attribute,
            HOOK_ATTRIBUTE,
            None,
        )

        if definition is None:
            continue

        hooks.setdefault(definition.hook_type, []).append(
            attribute
        )

    ordered = {
        hook_type: tuple(sorted(
            functions,
            key=lambda function: getattr(
                function,
                HOOK_ATTRIBUTE,
            ).priority,
        ))
        for hook_type, functions in hooks.items()
    }

    return MappingProxyType(ordered)


# =========================================================
# Safe Service Access
# =========================================================


class ServiceProxy:
    """
    Read-only service access abstraction.

    Prevents plugins from mutating runtime service containers.
    """

    __slots__ = ("_services",)

    def __init__(
        self,
        services: Mapping[str, Any],
    ) -> None:
        self._services = MappingProxyType(dict(services))

    def get(self, name: str) -> Any:
        return self._services.get(name)

    def require(self, name: str) -> Any:
        if name not in self._services:
            raise SDKError(
                f"Required service '{name}' unavailable"
            )

        return self._services[name]

    def available(self) -> Tuple[str, ...]:
        return tuple(sorted(self._services.keys()))


# =========================================================
# Public Exports
# =========================================================


__all__ = [
    "SDK_VERSION",
    "SDK_API_VERSION",
    "SDKError",
    "CapabilityError",
    "HookValidationError",
    "CompatibilityError",
    "ContractViolationError",
    "PluginMetadata",
    "PluginContext",
    "HookDefinition",
    "PluginBase",
    "LifecycleProtocol",
    "HealthcheckProtocol",
    "register_plugin",
    "hook",
    "event_hook",
    "service_hook",
    "route_hook",
    "capability",
    "CompatibilityLayer",
    "ServiceProxy",
    "get_registered_plugins",
    "get_plugin_class",
    "get_plugin_metadata",
    "get_hooks",
]
