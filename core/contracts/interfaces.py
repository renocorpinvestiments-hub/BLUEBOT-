# ============================================================
# core/contracts/interfaces.py
# ============================================================
# Institutional Runtime Interfaces
# ============================================================
#
# RESPONSIBILITIES
# ------------------------------------------------------------
# - system capability contracts
# - plugin capability boundaries
# - infrastructure abstraction
# - transport abstraction
# - event runtime contracts
# - provider definitions
# - swappable implementations
# - distributed-system compatibility
#
# DESIGN GOALS
# ------------------------------------------------------------
# - async-first
# - scalable
# - production-safe
# - plugin-ready
# - microservice-ready
# - broker-compatible
# - event-driven
# - infrastructure-agnostic
# - institutionally modular
# - future orchestration compatible
#
# IMPORTANT
# ------------------------------------------------------------
# THIS FILE MUST NEVER:
# - contain business logic
# - import FastAPI
# - import Redis/Kafka/Postgres
# - contain implementations
# - know infrastructure vendors
#
# THIS FILE DEFINES:
# - contracts
# - runtime capabilities
# - provider boundaries
# - infrastructure abstractions
#
# ============================================================

from __future__ import annotations

from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

from core.contracts.events import Event
from core.contracts.messages import (
    Message,
    CommandMessage,
    ResponseMessage,
)


# ============================================================
# EVENT HANDLER
# ============================================================

EventHandler = Callable[
    [Event],
    Awaitable[None],
]


# ============================================================
# MESSAGE HANDLER
# ============================================================

MessageHandler = Callable[
    [Message],
    Awaitable[Optional[ResponseMessage]],
]


# ============================================================
# EVENT BUS INTERFACE
# ============================================================

@runtime_checkable
class EventBusInterface(Protocol):
    """
    Event-driven runtime bus contract.

    Future compatible with:
    - Kafka
    - RabbitMQ
    - Redis Streams
    - NATS
    - internal async buses
    """

    async def publish(
        self,
        event: Event,
    ) -> None:
        """
        Publish runtime event.
        """
        ...

    async def publish_many(
        self,
        events: List[Event],
    ) -> None:
        """
        Bulk event publishing.
        """
        ...

    async def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
    ) -> None:
        """
        Subscribe to runtime events.
        """
        ...

    async def unsubscribe(
        self,
        event_type: str,
        handler: EventHandler,
    ) -> None:
        """
        Remove event subscription.
        """
        ...

    async def drain(self) -> None:
        """
        Gracefully drain pending events.
        """
        ...


# ============================================================
# MESSAGE BUS INTERFACE
# ============================================================

@runtime_checkable
class MessageBusInterface(Protocol):
    """
    Internal message transport contract.

    Supports:
    - commands
    - requests
    - responses
    - broker migration later
    """

    async def send(
        self,
        message: Message,
    ) -> Optional[ResponseMessage]:
        """
        Send internal message.
        """
        ...

    async def register_handler(
        self,
        message_type: str,
        handler: MessageHandler,
    ) -> None:
        """
        Register message handler.
        """
        ...

    async def unregister_handler(
        self,
        message_type: str,
        handler: MessageHandler,
    ) -> None:
        """
        Remove handler registration.
        """
        ...


# ============================================================
# BROKER INTERFACE
# ============================================================

@runtime_checkable
class BrokerInterface(Protocol):
    """
    Distributed broker abstraction.

    Future compatible with:
    - Kafka
    - RabbitMQ
    - Redis Streams
    - NATS
    """

    async def connect(self) -> None:
        ...

    async def disconnect(self) -> None:
        ...

    async def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
    ) -> None:
        ...

    async def consume(
        self,
        topic: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        ...

    async def acknowledge(
        self,
        message_id: str,
    ) -> None:
        ...

    async def healthcheck(self) -> bool:
        ...


# ============================================================
# CACHE INTERFACE
# ============================================================

@runtime_checkable
class CacheInterface(Protocol):
    """
    Multi-layer cache abstraction.

    Future compatible with:
    - Redis
    - in-memory cache
    - distributed cache
    """

    async def get(
        self,
        key: str,
    ) -> Optional[Any]:
        ...

    async def set(
        self,
        key: str,
        value: Any,
        *,
        ttl: Optional[int] = None,
    ) -> None:
        ...

    async def delete(
        self,
        key: str,
    ) -> None:
        ...

    async def exists(
        self,
        key: str,
    ) -> bool:
        ...

    async def clear(self) -> None:
        ...


# ============================================================
# TELEMETRY INTERFACE
# ============================================================

@runtime_checkable
class TelemetryInterface(Protocol):
    """
    Runtime observability abstraction.
    """

    async def increment(
        self,
        metric: str,
        value: int = 1,
    ) -> None:
        ...

    async def gauge(
        self,
        metric: str,
        value: float,
    ) -> None:
        ...

    async def timing(
        self,
        metric: str,
        duration_ms: float,
    ) -> None:
        ...

    async def snapshot(self) -> Dict[str, Any]:
        ...


# ============================================================
# RUNTIME STATE INTERFACE
# ============================================================

@runtime_checkable
class RuntimeStateInterface(Protocol):
    """
    Runtime state abstraction.
    """

    async def snapshot(self) -> Dict[str, Any]:
        ...

    async def health_graph(self) -> Dict[str, Any]:
        ...

    async def set_status(
        self,
        status: Any,
    ) -> None:
        ...


# ============================================================
# SUPERVISOR INTERFACE
# ============================================================

@runtime_checkable
class SupervisorInterface(Protocol):
    """
    Runtime supervision abstraction.
    """

    async def register_task(
        self,
        name: str,
        coroutine_factory: Callable[
            [],
            Awaitable[Any],
        ],
    ) -> None:
        ...

    async def start_task(
        self,
        name: str,
    ) -> None:
        ...

    async def shutdown(self) -> None:
        ...

    async def snapshot(self) -> Dict[str, Any]:
        ...


# ============================================================
# PLUGIN INTERFACE
# ============================================================

@runtime_checkable
class PluginInterface(Protocol):
    """
    Runtime plugin contract.

    Enables:
    - isolated plugin loading
    - capability discovery
    - hot-pluggable modules
    - future plugin marketplace
    """

    name: str

    version: str

    async def initialize(self) -> None:
        """
        Initialize plugin resources.
        """
        ...

    async def start(self) -> None:
        """
        Start plugin runtime.
        """
        ...

    async def stop(self) -> None:
        """
        Gracefully stop plugin.
        """
        ...

    async def healthcheck(self) -> bool:
        """
        Plugin health verification.
        """
        ...

    async def metadata(self) -> Dict[str, Any]:
        """
        Plugin metadata exposure.
        """
        ...


# ============================================================
# DISCOVERY INTERFACE
# ============================================================

@runtime_checkable
class DiscoveryInterface(Protocol):
    """
    Runtime discovery abstraction.

    Supports:
    - module discovery
    - plugin discovery
    - distributed discovery later
    """

    async def discover(self) -> List[str]:
        ...

    async def reload(self) -> None:
        ...


# ============================================================
# STORAGE INTERFACE
# ============================================================

@runtime_checkable
class StorageInterface(Protocol):
    """
    Persistence abstraction.

    Future compatible with:
    - PostgreSQL
    - MySQL
    - distributed databases
    - object storage
    """

    async def save(
        self,
        key: str,
        value: Dict[str, Any],
    ) -> None:
        ...

    async def load(
        self,
        key: str,
    ) -> Optional[Dict[str, Any]]:
        ...

    async def delete(
        self,
        key: str,
    ) -> None:
        ...

    async def exists(
        self,
        key: str,
    ) -> bool:
        ...


# ============================================================
# QUEUE INTERFACE
# ============================================================

@runtime_checkable
class QueueInterface(Protocol):
    """
    Async queue abstraction.

    Future compatible with:
    - RabbitMQ
    - Kafka
    - Redis Streams
    - SQS
    """

    async def enqueue(
        self,
        item: Dict[str, Any],
    ) -> None:
        ...

    async def dequeue(
        self,
    ) -> Optional[Dict[str, Any]]:
        ...

    async def size(self) -> int:
        ...

    async def purge(self) -> None:
        ...


# ============================================================
# AUTH INTERFACE
# ============================================================

@runtime_checkable
class AuthProviderInterface(Protocol):
    """
    Authentication provider abstraction.

    Future compatible with:
    - JWT
    - OAuth
    - API Keys
    - external identity providers
    """

    async def authenticate(
        self,
        credentials: Dict[str, Any],
    ) -> bool:
        ...

    async def authorize(
        self,
        subject: str,
        action: str,
    ) -> bool:
        ...


# ============================================================
# LIFECYCLE INTERFACE
# ============================================================

@runtime_checkable
class LifecycleInterface(Protocol):
    """
    Runtime lifecycle contract.
    """

    async def initialize(self) -> None:
        ...

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    async def healthcheck(self) -> bool:
        ...


# ============================================================
# SERIALIZATION INTERFACE
# ============================================================

@runtime_checkable
class SerializerInterface(Protocol):
    """
    Transport-safe serialization abstraction.

    Future compatible with:
    - JSON
    - MessagePack
    - Avro
    - Protobuf
    """

    async def serialize(
        self,
        payload: Any,
    ) -> bytes:
        ...

    async def deserialize(
        self,
        payload: bytes,
    ) -> Any:
        ...


# ============================================================
# CLOCK INTERFACE
# ============================================================

@runtime_checkable
class ClockInterface(Protocol):
    """
    Injectable runtime clock abstraction.

    Critical for:
    - deterministic testing
    - replay systems
    - distributed consistency
    """

    async def now(self) -> float:
        ...


# ============================================================
# ID GENERATOR INTERFACE
# ============================================================

@runtime_checkable
class IdGeneratorInterface(Protocol):
    """
    Runtime-safe ID generation abstraction.

    Future compatible with:
    - UUIDv4
    - ULID
    - Snowflake IDs
    - distributed IDs
    """

    async def generate(self) -> str:
        ...
