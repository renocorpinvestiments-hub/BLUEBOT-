# core/kernel/container.py
"""
Institutional Dependency Injection Container

Responsibilities:
- service registration
- lazy resolution
- singleton + factory support
- thread-safe access
- circular dependency detection
- idempotent operations
- failure-safe resolution

Design Goals:
- O(1) lookups
- zero global side-effects
- minimal locking
- retry-safe factories
- production-grade error handling
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional, Set


# ============================================================
# ERRORS
# ============================================================

class ContainerError(Exception):
    """Base container error"""


class ServiceNotFound(ContainerError):
    pass


class ServiceAlreadyRegistered(ContainerError):
    pass


class CircularDependencyError(ContainerError):
    pass


class ServiceInitializationError(ContainerError):
    pass


# ============================================================
# CONTAINER
# ============================================================

class Container:
    """
    High-performance DI container.
    """

    def __init__(self) -> None:
        self._services: Dict[str, Any] = {}
        self._factories: Dict[str, Callable[[], Any]] = {}
        self._locks: Dict[str, threading.Lock] = {}

        # For circular dependency detection
        self._resolving: threading.local = threading.local()

        # Global lock for registration only (not resolution)
        self._registry_lock = threading.Lock()

    # ========================================================
    # REGISTRATION
    # ========================================================

    def register(
        self,
        name: str,
        instance: Any,
        *,
        override: bool = False,
    ) -> None:
        """
        Register a singleton instance.

        Idempotent by default unless override=True.
        """

        if not name:
            raise ValueError("Service name cannot be empty")

        with self._registry_lock:
            if name in self._services and not override:
                return  # idempotent

            self._services[name] = instance

    def register_factory(
        self,
        name: str,
        factory: Callable[[], Any],
        *,
        override: bool = False,
    ) -> None:
        """
        Register a lazy factory.

        Factory is only executed on first resolve.
        """

        if not callable(factory):
            raise ValueError(f"Factory for '{name}' must be callable")

        with self._registry_lock:
            if name in self._factories and not override:
                return  # idempotent

            self._factories[name] = factory
            self._locks[name] = threading.Lock()

    # ========================================================
    # RESOLUTION
    # ========================================================

    def get(self, name: str) -> Any:
        """
        Resolve a service.

        - O(1) lookup
        - thread-safe lazy init
        - circular dependency detection
        """

        # Fast path (no lock)
        if name in self._services:
            return self._services[name]

        if name not in self._factories:
            raise ServiceNotFound(f"Service '{name}' not found")

        # Ensure resolving stack exists
        if not hasattr(self._resolving, "stack"):
            self._resolving.stack = set()

        # Circular dependency detection
        if name in self._resolving.stack:
            raise CircularDependencyError(
                f"Circular dependency detected while resolving '{name}'"
            )

        lock = self._locks[name]

        with lock:
            # Double-check after acquiring lock
            if name in self._services:
                return self._services[name]

            self._resolving.stack.add(name)

            try:
                instance = self._factories[name]()

                if instance is None:
                    raise ServiceInitializationError(
                        f"Factory for '{name}' returned None"
                    )

                # Cache singleton
                self._services[name] = instance

                return instance

            except Exception as e:
                # Do NOT cache failure → allows retry
                raise ServiceInitializationError(
                    f"Failed to initialize service '{name}': {e}"
                ) from e

            finally:
                self._resolving.stack.remove(name)

    # ========================================================
    # UTILITIES
    # ========================================================

    def has(self, name: str) -> bool:
        """Check if service or factory exists."""
        return name in self._services or name in self._factories

    def remove(self, name: str) -> None:
        """Remove a service safely."""

        with self._registry_lock:
            self._services.pop(name, None)
            self._factories.pop(name, None)
            self._locks.pop(name, None)

    def clear(self) -> None:
        """Reset container (mainly for testing)."""

        with self._registry_lock:
            self._services.clear()
            self._factories.clear()
            self._locks.clear()

    def list_services(self) -> Set[str]:
        """Return all registered service names."""
        return set(self._services.keys()) | set(self._factories.keys())
