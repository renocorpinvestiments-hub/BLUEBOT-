# ============================================================
# core/contracts/messages.py
# ============================================================
# Institutional Runtime Message Contracts
# ============================================================
#
# RESPONSIBILITIES
# ------------------------------------------------------------
# - command contracts
# - response contracts
# - transport-safe messaging
# - broker-compatible payloads
# - retry-safe communication
# - distributed runtime readiness
# - plugin-safe message abstraction
# - internal DTO standardization
#
# DESIGN GOALS
# ------------------------------------------------------------
# - scalable
# - async-first
# - immutable
# - production-safe
# - broker compatible
# - plugin-ready
# - transport agnostic
# - event-driven
# - microservice-ready
# - institutionally correct
#
# IMPORTANT
# ------------------------------------------------------------
# THIS FILE MUST NOT:
# - contain business logic
# - know Redis/Kafka/RabbitMQ
# - depend on FastAPI
# - depend on databases
# - know infrastructure vendors
#
# THIS FILE IS:
# - the universal internal transport layer
# - command/response standardization
# - distributed-safe communication contracts
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
# MESSAGE TYPES
# ============================================================

class MessageType(str, Enum):
    """
    Runtime message classification.
    """

    COMMAND = "command"

    RESPONSE = "response"

    ACKNOWLEDGEMENT = "acknowledgement"

    HEARTBEAT = "heartbeat"

    SYSTEM = "system"

    ERROR = "error"


# ============================================================
# MESSAGE PRIORITY
# ============================================================

class MessagePriority(str, Enum):
    """
    Execution priority.

    Future-compatible with:
    - queue prioritization
    - broker QoS
    - overload management
    """

    LOW = "low"

    NORMAL = "normal"

    HIGH = "high"

    CRITICAL = "critical"


# ============================================================
# DELIVERY STATUS
# ============================================================

class DeliveryStatus(str, Enum):
    """
    Delivery lifecycle state.
    """

    PENDING = "pending"

    PROCESSING = "processing"

    ACKNOWLEDGED = "acknowledged"

    FAILED = "failed"

    RETRYING = "retrying"

    DEAD_LETTER = "dead_letter"


# ============================================================
# MESSAGE METADATA
# ============================================================

@dataclass(slots=True)
class MessageMetadata:
    """
    Standardized runtime metadata.

    Future-compatible with:
    - Kafka headers
    - distributed tracing
    - dead-letter queues
    - broker replication
    - cross-region routing
    """

    message_id: str

    created_at: float

    source: str

    version: str = "1.0"

    correlation_id: Optional[str] = None

    causation_id: Optional[str] = None

    trace_id: Optional[str] = None

    span_id: Optional[str] = None

    deduplication_id: Optional[str] = None

    retry_count: int = 0

    timeout_ms: Optional[int] = None

    priority: MessagePriority = (
        MessagePriority.NORMAL
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
        deduplication_id: Optional[
            str
        ] = None,
        timeout_ms: Optional[int] = None,
        priority: MessagePriority = (
            MessagePriority.NORMAL
        ),
        tags: Optional[
            Dict[str, str]
        ] = None,
    ) -> "MessageMetadata":

        return cls(
            message_id=uuid.uuid4().hex,
            created_at=time.time(),
            source=source,
            version=version,
            correlation_id=correlation_id,
            causation_id=causation_id,
            trace_id=trace_id,
            span_id=span_id,
            deduplication_id=(
                deduplication_id
                or uuid.uuid4().hex
            ),
            timeout_ms=timeout_ms,
            priority=priority,
            tags=tags or {},
        )

    def snapshot(self) -> Dict[str, Any]:

        return {
            "message_id": self.message_id,
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
            "deduplication_id": (
                self.deduplication_id
            ),
            "retry_count": self.retry_count,
            "timeout_ms": self.timeout_ms,
            "priority": (
                self.priority.value
            ),
            "tags": deepcopy(self.tags),
        }


# ============================================================
# BASE MESSAGE
# ============================================================

@dataclass(slots=True)
class BaseMessage:
    """
    Canonical runtime transport contract.

    ALL runtime messages should inherit from this.

    Examples:
    - credit_wallet_request
    - dispatch_event_command
    - broker_acknowledgement
    - worker_heartbeat
    - retry_instruction

    This class is:
    - immutable by convention
    - broker-safe
    - plugin-safe
    - transport agnostic
    """

    name: str

    message_type: MessageType

    payload: Dict[str, Any]

    metadata: MessageMetadata

    status: DeliveryStatus = (
        DeliveryStatus.PENDING
    )

    routing_key: Optional[str] = None

    partition_key: Optional[str] = None

    reply_to: Optional[str] = None

    schema: str = "core.message"

    expires_at: Optional[float] = None

    def snapshot(self) -> Dict[str, Any]:
        """
        Immutable transport snapshot.

        Useful for:
        - brokers
        - retries
        - persistence
        - dead-letter queues
        - diagnostics
        """

        return {
            "name": self.name,
            "message_type": (
                self.message_type.value
            ),
            "status": self.status.value,
            "routing_key": self.routing_key,
            "partition_key": (
                self.partition_key
            ),
            "reply_to": self.reply_to,
            "schema": self.schema,
            "expires_at": self.expires_at,
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

        Future-compatible with:
        - Redis Streams
        - Kafka
        - RabbitMQ
        - NATS
        - SQS
        """

        return self.snapshot()

    @property
    def expired(self) -> bool:
        """
        TTL validation support.
        """

        if self.expires_at is None:
            return False

        return time.time() > self.expires_at

    @classmethod
    def create(
        cls,
        *,
        name: str,
        message_type: MessageType,
        payload: Dict[str, Any],
        source: str,
        routing_key: Optional[str] = None,
        partition_key: Optional[str] = None,
        reply_to: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        deduplication_id: Optional[
            str
        ] = None,
        timeout_ms: Optional[int] = None,
        expires_in_seconds: Optional[
            int
        ] = None,
        priority: MessagePriority = (
            MessagePriority.NORMAL
        ),
        schema: str = "core.message",
        version: str = "1.0",
        tags: Optional[
            Dict[str, str]
        ] = None,
    ) -> "BaseMessage":

        metadata = MessageMetadata.create(
            source=source,
            version=version,
            correlation_id=correlation_id,
            causation_id=causation_id,
            trace_id=trace_id,
            span_id=span_id,
            deduplication_id=(
                deduplication_id
            ),
            timeout_ms=timeout_ms,
            priority=priority,
            tags=tags,
        )

        expires_at = None

        if expires_in_seconds is not None:
            expires_at = (
                time.time()
                + expires_in_seconds
            )

        return cls(
            name=name,
            message_type=message_type,
            payload=deepcopy(payload),
            metadata=metadata,
            routing_key=routing_key,
            partition_key=partition_key,
            reply_to=reply_to,
            schema=schema,
            expires_at=expires_at,
        )


# ============================================================
# COMMAND MESSAGE
# ============================================================

@dataclass(slots=True)
class CommandMessage(BaseMessage):
    """
    Imperative runtime instruction.

    Meaning:
    "Please do something."

    Examples:
    - create_wallet
    - dispatch_event
    - process_reward
    """

    pass


# ============================================================
# RESPONSE MESSAGE
# ============================================================

@dataclass(slots=True)
class ResponseMessage(BaseMessage):
    """
    Runtime response contract.

    Examples:
    - wallet_created_response
    - broker_connected_response
    """

    success: bool = True

    error: Optional[str] = None


# ============================================================
# ACKNOWLEDGEMENT MESSAGE
# ============================================================

@dataclass(slots=True)
class AckMessage(BaseMessage):
    """
    Delivery acknowledgement.

    Critical later for:
    - brokers
    - retries
    - distributed guarantees
    """

    acknowledged_message_id: Optional[
        str
    ] = None


# ============================================================
# HEARTBEAT MESSAGE
# ============================================================

@dataclass(slots=True)
class HeartbeatMessage(BaseMessage):
    """
    Runtime heartbeat contract.

    Useful for:
    - worker supervision
    - health monitoring
    - distributed runtimes
    """

    worker_id: Optional[str] = None

    health_score: Optional[float] = None


# ============================================================
# ERROR MESSAGE
# ============================================================

@dataclass(slots=True)
class ErrorMessage(BaseMessage):
    """
    Runtime error transport.

    Useful for:
    - dead-letter systems
    - diagnostics
    - retry orchestration
    """

    error_code: Optional[str] = None

    error_message: Optional[str] = None

    recoverable: bool = True


# ============================================================
# MESSAGE FACTORY
# ============================================================

class MessageFactory:
    """
    Institutional runtime message factory.

    Centralizes:
    - metadata standards
    - schema consistency
    - transport safety
    - future validation hooks
    """

    @staticmethod
    def command(
        *,
        name: str,
        payload: Dict[str, Any],
        source: str,
        priority: MessagePriority = (
            MessagePriority.NORMAL
        ),
        **kwargs: Any,
    ) -> CommandMessage:

        return CommandMessage.create(
            name=name,
            message_type=(
                MessageType.COMMAND
            ),
            payload=payload,
            source=source,
            priority=priority,
            **kwargs,
        )

    @staticmethod
    def response(
        *,
        name: str,
        payload: Dict[str, Any],
        source: str,
        success: bool = True,
        error: Optional[str] = None,
        priority: MessagePriority = (
            MessagePriority.NORMAL
        ),
        **kwargs: Any,
    ) -> ResponseMessage:

        message = ResponseMessage.create(
            name=name,
            message_type=(
                MessageType.RESPONSE
            ),
            payload=payload,
            source=source,
            priority=priority,
            **kwargs,
        )

        message.success = success
        message.error = error

        return message

    @staticmethod
    def acknowledgement(
        *,
        name: str,
        payload: Dict[str, Any],
        source: str,
        acknowledged_message_id: str,
        **kwargs: Any,
    ) -> AckMessage:

        message = AckMessage.create(
            name=name,
            message_type=(
                MessageType.ACKNOWLEDGEMENT
            ),
            payload=payload,
            source=source,
            **kwargs,
        )

        message.acknowledged_message_id = (
            acknowledged_message_id
        )

        return message

    @staticmethod
    def heartbeat(
        *,
        source: str,
        worker_id: str,
        health_score: float,
        payload: Optional[
            Dict[str, Any]
        ] = None,
        **kwargs: Any,
    ) -> HeartbeatMessage:

        message = HeartbeatMessage.create(
            name="worker.heartbeat",
            message_type=(
                MessageType.HEARTBEAT
            ),
            payload=payload or {},
            source=source,
            **kwargs,
        )

        message.worker_id = worker_id
        message.health_score = (
            health_score
        )

        return message

    @staticmethod
    def error(
        *,
        source: str,
        error_message: str,
        recoverable: bool = True,
        error_code: Optional[str] = None,
        payload: Optional[
            Dict[str, Any]
        ] = None,
        **kwargs: Any,
    ) -> ErrorMessage:

        message = ErrorMessage.create(
            name="runtime.error",
            message_type=MessageType.ERROR,
            payload=payload or {},
            source=source,
            **kwargs,
        )

        message.error_message = (
            error_message
        )

        message.error_code = error_code

        message.recoverable = recoverable

        return message


# ============================================================
# MESSAGE VALIDATION
# ============================================================

def validate_message(
    message: BaseMessage,
) -> None:
    """
    Minimal institutional validation layer.

    Future-compatible with:
    - schema registry
    - protobuf
    - avro
    - broker validation
    """

    if not message.name:
        raise ValueError(
            "Message name cannot be empty."
        )

    if not isinstance(
        message.payload,
        dict,
    ):
        raise TypeError(
            "Message payload must be a dictionary."
        )

    if not message.metadata.source:
        raise ValueError(
            "Message source cannot be empty."
        )

    if message.expired:
        raise ValueError(
            "Message has expired."
)
