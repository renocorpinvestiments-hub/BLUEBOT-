"""
core/kernel/runtime.py

Institutional Runtime Kernel

Responsibilities:
- System boot orchestration
- Container + Discovery integration
- Lifecycle management
- Fault isolation & graceful degradation
- Idempotent startup
- Observability + health tracking
- Auto-adjustment for scale

Design Principles:
- Boot once, run forever
- Lazy everything
- Fail isolated, not global
- Deterministic startup
- No business logic
"""

from __future__ import annotations

import threading
import time
import logging
from typing import Any, Dict, Optional, Callable, List

from core.kernel.container import get_container
from core.kernel.discovery import get_discovery

logger = logging.getLogger("kernel.runtime")


# ============================================================
# Exceptions
# ============================================================

class RuntimeErrorBase(Exception):
    pass


class BootError(RuntimeErrorBase):
    pass


# ============================================================
# Runtime Kernel
# ============================================================

class RuntimeKernel:
    """
    Institutional-grade system orchestrator.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

        # Core systems
        self._container = get_container()
        self._discovery = get_discovery()

        # State
        self._booted = False
        self._boot_time: Optional[float] = None

        # Lifecycle hooks
        self._pre_init_hooks: List[Callable[[], None]] = []
        self._post_init_hooks: List[Callable[[], None]] = []

        # Health & observability
        self._health: Dict[str, Any] = {
            "status": "idle",
            "errors": 0,
            "warnings": 0,
        }

        # Config
        self._strict_mode = False
        self._graceful_degradation = True

    # ========================================================
    # Public API
    # ========================================================

    def boot(self) -> None:
        """
        Idempotent system boot.
        """
        if self._booted:
            logger.info("[RUNTIME] Boot already completed — skipping")
            return

        with self._lock:
            if self._booted:
                return

            start_time = time.time()
            self._health["status"] = "booting"

            try:
                self._run_phase("pre_init", self._run_pre_init)
                self._run_phase("discovery", self._run_discovery)
                self._run_phase("registration", self._run_registration)
                self._run_phase("post_init", self._run_post_init)

                self._booted = True
                self._boot_time = time.time() - start_time
                self._health["status"] = "running"

                logger.info(f"[RUNTIME] Boot complete in {self._boot_time:.4f}s")

            except Exception as e:
                self._health["status"] = "failed"
                logger.critical("[RUNTIME] Boot failed", exc_info=True)

                if self._strict_mode:
                    raise BootError("System boot failed") from e

                if not self._graceful_degradation:
                    raise

    def get(self, name: str) -> Any:
        return self._container.get(name)

    def has(self, name: str) -> bool:
        return self._container.has(name)

    def register(self, name: str, value: Any) -> None:
        self._container.register(name, value)

    # ========================================================
    # Boot Phases
    # ========================================================

    def _run_phase(self, name: str, fn: Callable[[], None]) -> None:
        """
        Execute a boot phase with isolation.
        """
        try:
            logger.info(f"[RUNTIME] Phase: {name}")
            fn()

        except Exception as e:
            self._health["errors"] += 1
            logger.error(f"[RUNTIME] Phase failed: {name} -> {e}", exc_info=True)

            if self._strict_mode:
                raise

    def _run_pre_init(self) -> None:
        for hook in self._pre_init_hooks:
            self._safe_execute(hook, "pre_init_hook")

    def _run_discovery(self) -> None:
        """
        Discover system modules.
        """
        try:
            routers = self._discovery.discover(
                "app.routers",
                validator=self._validate_router,
                namespace="routers",
            )

            plugins = self._discovery.discover(
                "app.plugins",
                validator=self._validate_plugin,
                namespace="plugins",
            )

            self._container.register("routers", routers)
            self._container.register("plugins", plugins)

        except Exception as e:
            self._handle_critical("discovery", e)

    def _run_registration(self) -> None:
        """
        Register core services.
        """
        try:
            # Example core services
            self._container.register_factory("timestamp", lambda: time.time)

        except Exception as e:
            self._handle_critical("registration", e)

    def _run_post_init(self) -> None:
        for hook in self._post_init_hooks:
            self._safe_execute(hook, "post_init_hook")

    # ========================================================
    # Validators
    # ========================================================

    def _validate_router(self, module: Any) -> Any:
        if hasattr(module, "router"):
            return module.router
        return None

    def _validate_plugin(self, module: Any) -> Any:
        if hasattr(module, "Plugin"):
            return module.Plugin()
        return None

    # ========================================================
    # Safety & Error Handling
    # ========================================================

    def _safe_execute(self, fn: Callable, label: str) -> None:
        try:
            fn()
        except Exception as e:
            self._health["warnings"] += 1
            logger.warning(f"[RUNTIME] {label} failed: {e}")

    def _handle_critical(self, phase: str, error: Exception) -> None:
        self._health["errors"] += 1
        logger.error(f"[RUNTIME] Critical failure in {phase}: {error}", exc_info=True)

        if self._strict_mode:
            raise RuntimeErrorBase(f"{phase} failed") from error

    # ========================================================
    # Hooks (Extensibility)
    # ========================================================

    def add_pre_init_hook(self, fn: Callable[[], None]) -> None:
        self._pre_init_hooks.append(fn)

    def add_post_init_hook(self, fn: Callable[[], None]) -> None:
        self._post_init_hooks.append(fn)

    # ========================================================
    # Observability
    # ========================================================

    def health(self) -> Dict[str, Any]:
        return {
            "status": self._health["status"],
            "errors": self._health["errors"],
            "warnings": self._health["warnings"],
            "boot_time": self._boot_time,
            "booted": self._booted,
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "container_services": len(self._container._services),
            "boot_time": self._boot_time,
        }

    # ========================================================
    # Config Controls
    # ========================================================

    def enable_strict_mode(self) -> None:
        self._strict_mode = True

    def disable_strict_mode(self) -> None:
        self._strict_mode = False

    def enable_graceful_degradation(self) -> None:
        self._graceful_degradation = True

    def disable_graceful_degradation(self) -> None:
        self._graceful_degradation = False


# ============================================================
# Singleton Access
# ============================================================

_runtime_instance: Optional[RuntimeKernel] = None
_runtime_lock = threading.Lock()


def get_runtime() -> RuntimeKernel:
    global _runtime_instance

    if _runtime_instance is None:
        with _runtime_lock:
            if _runtime_instance is None:
                _runtime_instance = RuntimeKernel()

    return _runtime_instance
