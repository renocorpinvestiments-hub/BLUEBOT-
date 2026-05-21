# core/events/models.py
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Generic, Mapping, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# TYPES
# ============================================================

PayloadT = TypeVar("PayloadT", bound=BaseModel)


# ============================================================
# BASE PAYLOAD
# ============================================================

class EventPayload(BaseModel):
    """
    Base payload contract.

    ALL event payloads should inherit from this.

    Example:
        class RewardPayload(EventPayload):
            user_id: str
            amount: int

    Rules:
    - immutable
    - serializable
    - transport-safe
    - queue-safe
    - no ORM objects
    - no DB sessions
    - no service instances
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=False,
    )


# ============================================================
# EVENT METADATA
# ============================================================

class EventMetadata(BaseModel):
    """
    Infrastructure-safe metadata container.

    Used for:
    - tracing
    - observability
    - retries
    - debugging
    - analytics
    - fraud analysis
    - distributed systems
    """

    model_config = ConfigDict(
        frozen=True,
        extra="allow",
    )

    environment: Optional[str] = None
    service: Optional[str] = None
    hostname: Optional[str] = None
    region: Optional[str] = None
    request_id: Optional[str] = None
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    tags: Dict[str, str] = Field(default_factory=dict)


# ============================================================
# BASE EVENT
# ============================================================

@dataclass(frozen=True, slots=True)
class Event(Generic[PayloadT]):
    """
    Institutional-grade immutable event contract.

    This is the SINGLE event structure across the platform.

    Designed for:
    - FastAPI
    - async systems
    - Redis
    - Celery
    - Kafka later
    - NATS later
    - microservices later
    - event replay
    - distributed tracing
    - observability
    - idempotent retries

    NEVER mutate events.
    NEVER attach live objects.
    """

    # ========================================================
    # REQUIRED CORE FIELDS
    # ========================================================

    event_name: str
    payload: PayloadT

    # ========================================================
    # VERSIONING
    # ========================================================

    event_version: str = "v1"

    # ========================================================
    # TRACEABILITY
    # ========================================================

    event_id: str = field(
        default_factory=lambda: str(uuid.uuid4())
    )

    correlation_id: str = field(
        default_factory=lambda: str(uuid.uuid4())
    )

    causation_id: Optional[str] = None

    # ========================================================
    # SAFETY / RETRY CONTROL
    # ========================================================

    idempotency_key: Optional[str] = None

    # ========================================================
    # SOURCE INFORMATION
    # ========================================================

    source: str = "unknown"

    # ========================================================
    # TIMESTAMPS
    # ========================================================

    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # ========================================================
    # INFRASTRUCTURE METADATA
    # ========================================================

    metadata: EventMetadata = field(
        default_factory=EventMetadata
    )

    # ========================================================
    # POST INIT
    # ========================================================

    def __post_init__(self) -> None:
        """
        Auto-generate deterministic idempotency key
        if not explicitly provided.

        Critical for:
        - retries
        - webhook safety
        - duplicate prevention
        - queue redelivery
        """

        if self.idempotency_key is None:
            generated = self._generate_idempotency_key()

            object.__setattr__(
                self,
                "idempotency_key",
                generated,
            )

    # ========================================================
    # SERIALIZATION
    # ========================================================

    def to_dict(self) -> Dict[str, Any]:
        """
        Transport-safe dictionary serialization.

        Compatible with:
        - Redis
        - Celery
        - Kafka
        - RabbitMQ
        - NATS
        - JSON APIs
        """

        return {
            "event_id": self.event_id,
            "event_name": self.event_name,
            "event_version": self.event_version,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload.model_dump(mode="json"),
            "metadata": self.metadata.model_dump(mode="json"),
        }

    def to_json(self) -> str:
        """
        Queue-safe JSON serialization.
        """

        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )

    # ========================================================
    # DESERIALIZATION
    # ========================================================

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        payload_model: type[PayloadT],
    ) -> "Event[PayloadT]":
        """
        Rebuild event from broker payload.

        Future compatible with:
        - Redis streams
        - Kafka consumers
        - dead-letter queues
        - event replay systems
        """

        payload = payload_model.model_validate(
            data["payload"]
        )

        metadata = EventMetadata.model_validate(
            data.get("metadata", {})
        )

        return cls(
            event_id=data["event_id"],
            event_name=data["event_name"],
            event_version=data.get(
                "event_version",
                "v1",
            ),
            timestamp=datetime.fromisoformat(
                data["timestamp"]
            ),
            source=data.get("source", "unknown"),
            correlation_id=data.get(
                "correlation_id",
                str(uuid.uuid4()),
            ),
            causation_id=data.get("causation_id"),
            idempotency_key=data.get(
                "idempotency_key"
            ),
            payload=payload,
            metadata=metadata,
        )

    @classmethod
    def from_json(
        cls,
        raw: str,
        payload_model: type[PayloadT],
    ) -> "Event[PayloadT]":
        """
        Deserialize broker-safe JSON payload.
        """

        return cls.from_dict(
            json.loads(raw),
            payload_model,
        )

    # ========================================================
    # INTERNALS
    # ========================================================

    def _generate_idempotency_key(self) -> str:
        """
        Deterministic retry-safe key.

        Important for:
        - duplicate event prevention
        - offerwall callbacks
        - payment systems
        - distributed retries
        """

        payload = {
            "event_name": self.event_name,
            "event_version": self.event_version,
            "source": self.source,
            "payload": self.payload.model_dump(
                mode="json"
            ),
        }

        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

        return hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()

    # ========================================================
    # DEBUGGING
    # ========================================================

    def __repr__(self) -> str:
        return (
            f"Event("
            f"name={self.event_name!r}, "
            f"id={self.event_id!r}, "
            f"version={self.event_version!r}"
            f")"
        )


# ============================================================
# FACTORY HELPERS
# ============================================================

def create_event(
    *,
    event_name: str,
    payload: PayloadT,
    source: str,
    event_version: str = "v1",
    correlation_id: Optional[str] = None,
    causation_id: Optional[str] = None,
    metadata: Optional[EventMetadata] = None,
) -> Event[PayloadT]:
    """
    Standardized event factory.

    Prevents inconsistent event creation
    across modules.

    Recommended usage:

        event = create_event(
            event_name="reward.completed",
            payload=RewardPayload(...),
            source="modules.rewards",
        )
    """

    return Event(
        event_name=event_name,
        payload=payload,
        source=source,
        event_version=event_version,
        correlation_id=(
            correlation_id or str(uuid.uuid4())
        ),
        causation_id=causation_id,
        metadata=metadata or EventMetadata(),
    )
