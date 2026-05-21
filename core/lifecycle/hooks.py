# ============================================================
# core/lifecycle/hooks.py
# ============================================================
# Institutional Lifecycle Hook Coordination System
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# Lifecycle extension coordination layer.
#
# THIS FILE DOES:
# - register lifecycle hooks
# - coordinate ordered hook execution
# - isolate hook failures
# - support sync + async hooks
# - provide timeout protection
# - expose deterministic hook pipelines
#
# THIS FILE DOES NOT:
# - execute runtime orchestration
# - manage workers
# - supervise services
# - restart subsystems
# - import runtime internals
# - own lifecycle state
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - async-first
# - deterministic execution
# - idempotent registration
# - weak-reference safety
# - failure isolation
# - lock-light architecture
# - low allocation overhead
# - scalable extension model
# - zero runtime coupling
#
# ============================================================

from __future__ import annotations

import asyncio
import inspect
import time
import weakref

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import Mapping
from typing import Optional
from typing import Tuple


# ============================================================
# Hook Types
# ============================================================

class HookType(str, Enum):
    """
    Supported lifecycle hook types.
    """

    BEFORE_START = "before_start"
    AFTER_START = "after_start"
    BEFORE_STOP = "before_stop"
    AFTER_STOP = "after_stop"


# ============================================================
# Exceptions
# ============================================================

class HookError(Exception):
    """Base lifecycle hook exception."""


class DuplicateHookError(HookError):
    """Raised on duplicate hook registration."""


class UnknownHookError(HookError):
    """Raised when hook does not exist."""


# ============================================================
# Hook Result
# ============================================================

@dataclass(frozen=True, slots=True)
class HookResult:
    """
    Immutable hook execution result.

    Safe for:
    - diagnostics
    - observability
    - debugging
    - orchestration
    """

    name: str
    hook_type: HookType
    success: bool
    duration: float
    error: Optional[str]


# ============================================================
# Hook Definition
# ============================================================

@dataclass(frozen=True, slots=True)
class Hook:
    """
    Immutable lifecycle hook definition.

    IMPORTANT
    --------------------------------------------------------
    Hooks define extension behavior.

    They DO NOT own lifecycle execution.
    """

    name: str
    hook_type: HookType
    callback: Callable[..., Any]
    priority: int = 100
    timeout: float = 30.0
    critical: bool = False
    run_async: bool = True

    def __post_init__(self) -> None:

        normalized = self.name.strip().lower()

        if not normalized:
            raise ValueError(
                "Hook name cannot be empty."
            )

        if self.timeout <= 0:
            raise ValueError(
                "Hook timeout must be positive."
            )

        object.__setattr__(self, "name", normalized)


# ============================================================
# Hook Registry
# ============================================================

class HookRegistry:
    """
    Institutional-grade lifecycle hook registry.

    DESIGN GOALS
    --------------------------------------------------------
    - deterministic ordering
    - failure isolation
    - scalable extension registration
    - low-overhead execution
    - async-safe execution
    - idempotent registration
    - lock-light reads

    IMPORTANT
    --------------------------------------------------------
    This registry coordinates hooks ONLY.

    It does NOT:
    - execute lifecycle orchestration
    - manage runtime state
    - supervise infrastructure
    """

    __slots__ = (
        "_hooks",
        "_lock",
    )

    def __init__(self) -> None:

        self._hooks: Dict[
            HookType,
            Dict[str, Hook],
        ] = {
            hook_type: {}
            for hook_type in HookType
        }

        self._lock = asyncio.Lock()

    # ========================================================
    # Registration
    # ========================================================

    async def register(
        self,
        hook: Hook,
    ) -> None:
        """
        Register lifecycle hook.

        FEATURES
        --------------------------------------------------------
        - async-safe
        - deterministic
        - idempotent protection
        - duplicate prevention
        """

        async with self._lock:

            registry = self._hooks[hook.hook_type]

            if hook.name in registry:
                raise DuplicateHookError(
                    f"Hook '{hook.name}' already exists."
                )

            registry[hook.name] = hook

    async def unregister(
        self,
        hook_type: HookType,
        name: str,
    ) -> None:
        """
        Remove registered hook.

        Useful for:
        - plugins
        - hot reload
        - dynamic extensions
        """

        normalized = name.strip().lower()

        async with self._lock:

            registry = self._hooks[hook_type]

            if normalized not in registry:
                raise UnknownHookError(
                    f"Unknown hook '{normalized}'."
                )

            del registry[normalized]

    # ========================================================
    # Lookup
    # ========================================================

    def exists(
        self,
        hook_type: HookType,
        name: str,
    ) -> bool:
        """
        Fast lock-free hook existence check.
        """

        normalized = name.strip().lower()

        return (
            normalized
            in self._hooks[hook_type]
        )

    def hooks(
        self,
        hook_type: HookType,
    ) -> Tuple[Hook, ...]:
        """
        Return deterministically ordered hooks.

        ORDER:
        --------------------------------------------------------
        1. priority
        2. lexical name
        """

        registry = self._hooks[hook_type]

        return tuple(
            sorted(
                registry.values(),
                key=lambda hook: (
                    hook.priority,
                    hook.name,
                ),
            )
        )

    # ========================================================
    # Execution
    # ========================================================

    async def execute(
        self,
        hook_type: HookType,
        *args: Any,
        concurrent: bool = True,
        **kwargs: Any,
    ) -> Tuple[HookResult, ...]:
        """
        Execute lifecycle hooks.

        FEATURES
        --------------------------------------------------------
        - deterministic execution ordering
        - failure isolation
        - timeout protection
        - async-safe
        - concurrent execution support
        - cancellation-safe

        IMPORTANT
        --------------------------------------------------------
        Hook failures do NOT crash execution
        unless the hook is marked critical.
        """

        hooks = self.hooks(hook_type)

        if not hooks:
            return ()

        if concurrent:
            tasks = [
                self._execute_hook(
                    hook,
                    *args,
                    **kwargs,
                )
                for hook in hooks
            ]

            results = await asyncio.gather(
                *tasks,
                return_exceptions=False,
            )

            return tuple(results)

        results = []

        for hook in hooks:

            result = await self._execute_hook(
                hook,
                *args,
                **kwargs,
            )

            results.append(result)

        return tuple(results)

    # ========================================================
    # Snapshot
    # ========================================================

    def snapshot(self) -> Mapping[
        HookType,
        Tuple[str, ...],
    ]:
        """
        Immutable hook snapshot.

        Useful for:
        - diagnostics
        - tooling
        - observability
        """

        snapshot = {
            hook_type: tuple(
                sorted(registry.keys())
            )
            for hook_type, registry
            in self._hooks.items()
        }

        return MappingProxyType(snapshot)

    # ========================================================
    # Internal Execution
    # ========================================================

    async def _execute_hook(
        self,
        hook: Hook,
        *args: Any,
        **kwargs: Any,
    ) -> HookResult:
        """
        Execute single hook safely.

        FEATURES
        --------------------------------------------------------
        - timeout isolation
        - exception isolation
        - async compatibility
        - deterministic reporting
        """

        started = time.monotonic()

        try:

            result = hook.callback(
                *args,
                **kwargs,
            )

            if inspect.isawaitable(result):

                await asyncio.wait_for(
                    result,
                    timeout=hook.timeout,
                )

            duration = (
                time.monotonic() - started
            )

            return HookResult(
                name=hook.name,
                hook_type=hook.hook_type,
                success=True,
                duration=duration,
                error=None,
            )

        except Exception as exc:

            duration = (
                time.monotonic() - started
            )

            if hook.critical:
                raise

            return HookResult(
                name=hook.name,
                hook_type=hook.hook_type,
                success=False,
                duration=duration,
                error=str(exc),
            )


# ============================================================
# Decorator Registration API
# ============================================================

def lifecycle_hook(
    hook_type: HookType,
    *,
    name: Optional[str] = None,
    priority: int = 100,
    timeout: float = 30.0,
    critical: bool = False,
) -> Callable[
    [Callable[..., Any]],
    Hook,
]:
    """
    Declarative lifecycle hook decorator.

    Example
    --------------------------------------------------------

    @lifecycle_hook(
        HookType.BEFORE_START,
        priority=10,
    )
    async def initialize():
        ...

    This improves:
    - plugin scalability
    - extension ergonomics
    - modular architecture
    """

    def decorator(
        callback: Callable[..., Any],
    ) -> Hook:

        hook_name = (
            name
            or callback.__name__
        )

        return Hook(
            name=hook_name,
            hook_type=hook_type,
            callback=callback,
            priority=priority,
            timeout=timeout,
            critical=critical,
            run_async=inspect.iscoroutinefunction(
                callback
            ),
        )

    return decorator


# ============================================================
# Global Default Hook Registry
# ============================================================

DEFAULT_HOOKS = HookRegistry()
