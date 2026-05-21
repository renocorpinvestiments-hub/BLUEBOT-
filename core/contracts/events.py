# ============================================================
# core/contracts/events.py
# ============================================================
# Institutional Event Contracts
# ============================================================
#
# RESPONSIBILITIES
# ------------------------------------------------------------
# - canonical event contracts
# - immutable event models
# - runtime-safe serialization
# - event metadata standardization
# - trace propagation foundation
# - broker compatibility layer
# - plugin-safe event abstraction
# - schema evolution readiness
#
# DESIGN GOALS
# ------------------------------------------------------------
# - async-first
# - scalable
# - event-driven
# - immutable
# - broker compatible
# - transport agnostic
# - plugin-safe
# - microservice-ready
# - production-safe
# - future schema evolution ready
#
# IMPORTANT
# ------------------------------------------------------------
# THIS FILE MUST NOT:
# - contain business logic
# - depend on FastAPI
# - depend on Redis/Kafka/NATS
# - know transport implementations
# - know infrastructure vendors
#
# THIS FILE IS:
# - the canonical event contract layer
# - the shared event language
# - runtime-safe event abstraction
#
# ============================================================

from __future__ import annotations

import time
import uuid

from copy import deepcopy
from dataclasses import (
    dataclass,
    field,
)
from enum import Enum
from typing import (
    Any,
    Dict,
    Optional,
)


# ============================================================
# EVENT PRIORITY
# ============================================================

class EventPriority(str, Enum):
    """
    Event execution priority.

    Useful later for:
    - broker QoS
    - queue prioritization
    - overload protection
    - adaptive scheduling
    """

    LOW = "low"

    NORMAL = "normal"

    HIGH = "high"

    CRITICAL = "critical"


# ============================================================
# EVENT CATEGORY
# ============================================================

class EventCategory(str, Enum):
    """
    High-level event classification.

    Useful for:
    - routing
    - analytics
    - observability
    - broker partitioning
    """

    SYSTEM = "system"

    DOMAIN = "domain"

    INFRASTRUCTURE = "infrastructure"

    SECURITY = "security"

    ANALYTICS = "analytics"

    PLUGIN = "plugin"


# ============================================================
# EVENT METADATA
# ============================================================

@dataclass(slots=True)
class EventMetadata:
    """
    Standardized event metadata.

    Future-compatible with:
    - Kafka headers
    - OpenTelemetry
    - distributed tracing
    - broker replication
    - multi-region routing
    """

    event_id: str

    created_at: float

    source: str

    version: str = "1.0"

    correlation_id: Optional[str] = None

    causation_id: Optional[str] = None

    trace_id: Optional[str] = None

    span_id: Optional[str] = None

    environment: Optional[str] = None

    region: Optional[str] = None

    retry_count: int = 0

    priority: EventPriority = (
        EventPriority.NORMAL
    )

    tags: Dict[str, str] = field(
        default_factory=dict
    )

    @classmethod
    def create(
        cls,
        *,
        source: str,
        version: str = "1.0",
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        priority: EventPriority = (
            EventPriority.NORMAL
        ),
        environment: Optional[str] = None,
        region: Optional[str] = None,
        tags: Optional[
            Dict[str, str]
        ] = None,
    ) -> "EventMetadata":

        return cls(
            event_id=uuid.uuid4().hex,
            created_at=time.time(),
            source=source,
            version=version,
            correlation_id=correlation_id,
            causation_id=causation_id,
            trace_id=trace_id,
            span_id=span_id,
            priority=priority,
            environment=environment,
            region=region,
            tags=tags or {},
        )

    def snapshot(self) -> Dict[str, Any]:

        return {
            "event_id": self.event_id,
            "created_at": self.created_at,
            "source": self.source,
            "version": self.version,
            "correlation_id": (
                self.correlation_id
            ),
            "causation_id": (
                self.causation_id
            ),
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "environment": self.environment,
            "region": self.region,
            "retry_count": self.retry_count,
            "priority": (
                self.priority.value
            ),
            "tags": deepcopy(self.tags),
        }


# ============================================================
# BASE EVENT CONTRACT
# ============================================================

@dataclass(slots=True)
class BaseEvent:
    """
    Canonical immutable event contract.

    ALL platform events should inherit from this.

    Examples:
    - wallet.credited
    - user.created
    - reward.completed
    - broker.connected
    - fraud.detected

    This is intentionally:
    - transport agnostic
    - broker agnostic
    - plugin safe
    - infrastructure independent
    """

    name: str

    category: EventCategory

    payload: Dict[str, Any]

    metadata: EventMetadata

    aggregate_id: Optional[str] = None

    partition_key: Optional[str] = None

    schema: str = "core.event"

    def snapshot(self) -> Dict[str, Any]:
        """
        Immutable event snapshot.

        Useful for:
        - brokers
        - persistence
        - retries
        - dead letter queues
        - diagnostics
        """

        return {
            "name": self.name,
            "category": (
                self.category.value
            ),
            "aggregate_id": (
                self.aggregate_id
            ),
            "partition_key": (
                self.partition_key
            ),
            "schema": self.schema,
            "metadata": (
                self.metadata.snapshot()
            ),
            "payload": deepcopy(
                self.payload
            ),
        }

    def serialize(self) -> Dict[str, Any]:
        """
        Transport-safe serialization.

        Future compatible with:
        - Redis
        - Kafka
        - RabbitMQ
        - NATS
        - SQS
        """

        return self.snapshot()

    @classmethod
    def create(
        cls,
        *,
        name: str,
        category: EventCategory,
        payload: Dict[str, Any],
        source: str,
        aggregate_id: Optional[str] = None,
        partition_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        priority: EventPriority = (
            EventPriority.NORMAL
        ),
        version: str = "1.0",
        schema: str = "core.event",
        tags: Optional[
            Dict[str, str]
        ] = None,
    ) -> "BaseEvent":

        metadata = EventMetadata.create(
            source=source,
            version=version,
            correlation_id=correlation_id,
            causation_id=causation_id,
            trace_id=trace_id,
            span_id=span_id,
            priority=priority,
            tags=tags,
        )

        return cls(
            name=name,
            category=category,
            payload=deepcopy(payload),
            metadata=metadata,
            aggregate_id=aggregate_id,
            partition_key=partition_key,
            schema=schema,
        )


# ============================================================
# SYSTEM EVENT
# ============================================================

@dataclass(slots=True)
class SystemEvent(BaseEvent):
    """
    Infrastructure/runtime event.

    Examples:
    - runtime.started
    - worker.failed
    - broker.connected
    - circuit.opened
    """

    pass


# ============================================================
# DOMAIN EVENT
# ============================================================

@dataclass(slots=True)
class DomainEvent(BaseEvent):
    """
    Business/domain event abstraction.

    Examples:
    - wallet.credited
    - offer.completed
    - reward.claimed
    """

    pass


# ============================================================
# PLUGIN EVENT
# ============================================================

@dataclass(slots=True)
class PluginEvent(BaseEvent):
    """
    Plugin-safe extension event.

    Important for:
    - external modules
    - extension runtime
    - future marketplace/plugins
    """

    plugin_name: Optional[str] = None


# ============================================================
# EVENT FACTORY
# ============================================================

class EventFactory:
    """
    Institutional event factory.

    Centralizes:
    - event creation
    - metadata standards
    - schema consistency
    - future validation hooks
    """

    @staticmethod
    def system(
        *,
        name: str,
        payload: Dict[str, Any],
        source: str,
        priority: EventPriority = (
            EventPriority.NORMAL
        ),
        **kwargs: Any,
    ) -> SystemEvent:

        return SystemEvent.create(
            name=name,
            category=EventCategory.SYSTEM,
            payload=payload,
            source=source,
            priority=priority,
            **kwargs,
        )

    @staticmethod
    def domain(
        *,
        name: str,
        payload: Dict[str, Any],
        source: str,
        priority: EventPriority = (
            EventPriority.NORMAL
        ),
        **kwargs: Any,
    ) -> DomainEvent:

        return DomainEvent.create(
            name=name,
            category=EventCategory.DOMAIN,
            payload=payload,
            source=source,
            priority=priority,
            **kwargs,
        )

    @staticmethod
    def plugin(
        *,
        name: str,
        payload: Dict[str, Any],
        source: str,
        plugin_name: str,
        priority: EventPriority = (
            EventPriority.NORMAL
        ),
        **kwargs: Any,
    ) -> PluginEvent:

        event = PluginEvent.create(
            name=name,
            category=EventCategory.PLUGIN,
            payload=payload,
            source=source,
            priority=priority,
            **kwargs,
        )

        event.plugin_name = plugin_name

        return event


# ============================================================
# EVENT VALIDATION
# ============================================================

def validate_event(
    event: BaseEvent,
) -> None:
    """
    Minimal institutional validation layer.

    Future-compatible with:
    - schema registry
    - protobuf/avro
    - broker validation
    - event version negotiation
    """

    if not event.name:
        raise ValueError(
            "Event name cannot be empty."
        )

    if not isinstance(
        event.payload,
        dict,
    ):
        raise TypeError(
            "Event payload must be a dictionary."
        )

    if not event.metadata.source:
        raise ValueError(
            "Event source cannot be empty."
)
