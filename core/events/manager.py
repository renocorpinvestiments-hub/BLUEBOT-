# ============================================================
# core/events/manager.py
# ============================================================
# Institutional-Grade Event Infrastructure Manager
# ============================================================
#
# RESPONSIBILITIES
# ------------------------------------------------------------
# - Event infrastructure lifecycle management
# - Listener auto-registration
# - Discovery integration
# - Middleware orchestration
# - Broker bridge initialization
# - Worker integration
# - Graceful startup/shutdown
# - Future distributed event routing
#
# DOES NOT:
# ------------------------------------------------------------
# - contain business logic
# - dispatch events directly
# - know application domains
# - know module internals
#
# ============================================================
# ARCHITECTURE ROLE
# ============================================================
#
# bus.py:
#     execution engine
#
# models.py:
#     contracts + schemas
#
# manager.py:
#     orchestration + lifecycle
#
# ============================================================
# FUTURE UPGRADE PATH
# ============================================================
#
# CURRENT:
# In-process event orchestration
#
# FUTURE:
# - Redis Pub/Sub
# - Redis Streams
# - RabbitMQ
# - NATS
# - Kafka
# - Celery bridges
# - Dead-letter queues
# - Broker replication
# - Distributed tracing
#
# WITHOUT rewriting modules.
#
# ============================================================

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from pathlib import Path
from types import ModuleType
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from core.events.bus import EventBus, event_bus

# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger("core.events.manager")

# ============================================================
# TYPE DEFINITIONS
# ============================================================

ListenerLoader = Callable[[], Awaitable[None]]

# ============================================================
# EVENT MANAGER
# ============================================================


class EventManager:
    """
    Institutional-grade event infrastructure manager.

    Responsibilities:
    --------------------------------------------------------
    - bootstrapping
    - auto-discovery
    - subscriber registration
    - middleware orchestration
    - broker preparation
    - worker bridge preparation
    - lifecycle management

    Core Philosophy:
    --------------------------------------------------------
    The manager coordinates infrastructure.

    The bus executes events.

    Modules remain isolated.
    """

    def __init__(
        self,
        *,
        bus: EventBus = event_bus,
        modules_path: str = "modules",
        enable_auto_discovery: bool = True,
    ) -> None:

        self.bus = bus

        self.modules_path = Path(modules_path)

        self.enable_auto_discovery = enable_auto_discovery

        self._initialized = False

        self._loaded_modules: Set[str] = set()

        self._middlewares: List[Any] = []

        self._broker_adapter: Optional[Any] = None

        self._startup_hooks: List[Callable[[], Awaitable[None]]] = []

        self._shutdown_hooks: List[Callable[[], Awaitable[None]]] = []

    # ========================================================
    # INITIALIZATION
    # ========================================================

    async def initialize(self) -> None:
        """
        Bootstraps event infrastructure.

        Startup Flow:
        ----------------------------------------------------
        1. Load listeners
        2. Register subscribers
        3. Mount middleware
        4. Prepare broker bridges
        5. Run startup hooks
        """

        if self._initialized:
            logger.warning(
                "EventManager already initialized."
            )
            return

        logger.info(
            "Initializing event infrastructure..."
        )

        # ====================================================
        # AUTO DISCOVERY
        # ====================================================

        if self.enable_auto_discovery:
            await self._discover_and_register()

        # ====================================================
        # STARTUP HOOKS
        # ====================================================

        await self._run_startup_hooks()

        self._initialized = True

        logger.info(
            "Event infrastructure initialized successfully."
        )

    # ========================================================
    # SHUTDOWN
    # ========================================================

    async def shutdown(self) -> None:
        """
        Graceful infrastructure shutdown.

        Safe for:
        ----------------------------------------------------
        - Render
        - Kubernetes
        - Docker
        - ASGI lifespan
        """

        logger.info(
            "Shutting down event infrastructure..."
        )

        await self._run_shutdown_hooks()

        await self.bus.shutdown()

        logger.info(
            "Event infrastructure shutdown complete."
        )

    # ========================================================
    # AUTO DISCOVERY
    # ========================================================

    async def _discover_and_register(self) -> None:
        """
        Auto-discovers module event listeners.

        Expected Structure:
        ----------------------------------------------------
        modules/
            wallet/
                events.py

            auth/
                events.py

        Each events.py should expose:

        async def register(event_bus):
            ...
        """

        if not self.modules_path.exists():
            logger.warning(
                "Modules path not found: %s",
                self.modules_path,
            )
            return

        logger.info(
            "Discovering module event listeners..."
        )

        for module_dir in self.modules_path.iterdir():

            if not module_dir.is_dir():
                continue

            events_file = module_dir / "events.py"

            if not events_file.exists():
                continue

            module_import_path = (
                f"{self.modules_path.name}."
                f"{module_dir.name}.events"
            )

            try:
                module = importlib.import_module(
                    module_import_path
                )

                await self._register_module(module)

                self._loaded_modules.add(
                    module_import_path
                )

                logger.info(
                    "Loaded event module: %s",
                    module_import_path,
                )

            except Exception as exc:
                logger.exception(
                    "Failed loading event module '%s': %s",
                    module_import_path,
                    exc,
                )

    # ========================================================
    # MODULE REGISTRATION
    # ========================================================

    async def _register_module(
        self,
        module: ModuleType,
    ) -> None:
        """
        Registers listeners from discovered module.
        """

        register = getattr(module, "register", None)

        if register is None:
            logger.warning(
                "Module missing register(): %s",
                module.__name__,
            )
            return

        # Async register
        if inspect.iscoroutinefunction(register):
            await register(self.bus)
            return

        # Sync fallback
        register(self.bus)

    # ========================================================
    # MIDDLEWARE SUPPORT
    # ========================================================

    def add_middleware(self, middleware: Any) -> None:
        """
        Registers event middleware.

        Future Middleware Examples:
        ----------------------------------------------------
        - metrics
        - tracing
        - logging
        - rate limiting
        - broker replication
        - dead-letter routing
        """

        self._middlewares.append(middleware)

        logger.info(
            "Event middleware registered: %s",
            middleware.__class__.__name__,
        )

    # ========================================================
    # BROKER INTEGRATION
    # ========================================================

    def attach_broker(self, broker_adapter: Any) -> None:
        """
        Attaches future broker adapter.

        Future adapters:
        ----------------------------------------------------
        - RedisEventBroker
        - KafkaEventBroker
        - NatsEventBroker
        - RabbitMQBroker
        """

        self._broker_adapter = broker_adapter

        logger.info(
            "Broker adapter attached: %s",
            broker_adapter.__class__.__name__,
        )

    # ========================================================
    # STARTUP HOOKS
    # ========================================================

    def add_startup_hook(
        self,
        hook: Callable[[], Awaitable[None]],
    ) -> None:

        self._startup_hooks.append(hook)

    async def _run_startup_hooks(self) -> None:

        if not self._startup_hooks:
            return

        logger.info(
            "Running %s startup hooks...",
            len(self._startup_hooks),
        )

        await asyncio.gather(
            *[
                self._safe_hook_execution(hook)
                for hook in self._startup_hooks
            ],
            return_exceptions=True,
        )

    # ========================================================
    # SHUTDOWN HOOKS
    # ========================================================

    def add_shutdown_hook(
        self,
        hook: Callable[[], Awaitable[None]],
    ) -> None:

        self._shutdown_hooks.append(hook)

    async def _run_shutdown_hooks(self) -> None:

        if not self._shutdown_hooks:
            return

        logger.info(
            "Running %s shutdown hooks...",
            len(self._shutdown_hooks),
        )

        await asyncio.gather(
            *[
                self._safe_hook_execution(hook)
                for hook in self._shutdown_hooks
            ],
            return_exceptions=True,
        )

    # ========================================================
    # SAFE HOOK EXECUTION
    # ========================================================

    async def _safe_hook_execution(
        self,
        hook: Callable[[], Awaitable[None]],
    ) -> None:

        try:

            if inspect.iscoroutinefunction(hook):
                await hook()
            else:
                hook()

        except Exception as exc:
            logger.exception(
                "Lifecycle hook failed: %s",
                exc,
            )

    # ========================================================
    # HEALTH / STATUS
    # ========================================================

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def loaded_modules(self) -> List[str]:
        return list(self._loaded_modules)

    @property
    def middleware_count(self) -> int:
        return len(self._middlewares)

    @property
    def broker_enabled(self) -> bool:
        return self._broker_adapter is not None


# ============================================================
# GLOBAL EVENT MANAGER
# ============================================================

event_manager = EventManager()

# ============================================================
# FASTAPI LIFESPAN EXAMPLE
# ============================================================
#
# from contextlib import asynccontextmanager
#
# @asynccontextmanager
# async def lifespan(app):
#
#     await event_manager.initialize()
#
#     yield
#
#     await event_manager.shutdown()
#
# ============================================================

# ============================================================
# MODULE EXAMPLE
# ============================================================
#
# modules/wallet/events.py
#
# async def reward_listener(event):
#     ...
#
# async def register(event_bus):
#
#     event_bus.subscribe(
#         "reward.completed",
#         reward_listener,
#     )
#
# ============================================================
