"""
RENOCORP CORE LOGGER FORMATTER
==============================

Institutional-grade immutable log transformation layer.

Responsibilities:
- structured log shaping
- schema normalization
- timestamp formatting
- payload sanitization
- secret redaction
- correlation enrichment
- deterministic serialization

DOES NOT:
- perform I/O
- manage sinks
- route logs
- manage telemetry
- calculate metrics
- perform tracing analysis

Architecture:
logger/
├── manager.py      <- runtime orchestration
├── formatter.py    <- THIS FILE
└── sinks.py        <- transport layer

Design Goals:
- zero side effects
- pure transformation pipeline
- immutable records
- schema versioning
- ultra-low allocation
- backward compatibility
- safe serialization
- hot-reload friendly
- plugin-safe formatting
"""

from __future__ import annotations

import json
import socket
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Final,
    FrozenSet,
    Iterable,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

# ============================================================
# CONSTANTS
# ============================================================

SCHEMA_VERSION: Final[str] = "1.0.0"

DEFAULT_REDACTION_TEXT: Final[str] = "[REDACTED]"

DEFAULT_SECRET_FIELDS: Final[FrozenSet[str]] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "private_key",
        "session",
        "cookie",
        "credit_card",
        "jwt",
    }
)

# ============================================================
# ENUMS
# ============================================================


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# ============================================================
# REDACTION POLICY
# ============================================================


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """
    Immutable redaction configuration.
    """

    enabled: bool = True
    replacement: str = DEFAULT_REDACTION_TEXT
    secret_fields: FrozenSet[str] = DEFAULT_SECRET_FIELDS
    recursive: bool = True


# ============================================================
# LOG CONTEXT
# ============================================================


@dataclass(frozen=True, slots=True)
class LogContext:
    """
    Immutable execution context.

    Lightweight metadata container.
    """

    service: str
    environment: str
    node: str = field(default_factory=socket.gethostname)
    version: str = "unknown"

    correlation_id: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None

    namespace: Optional[str] = None
    component: Optional[str] = None

    tags: Tuple[str, ...] = ()


# ============================================================
# LOG RECORD
# ============================================================


@dataclass(frozen=True, slots=True)
class LogRecord:
    """
    Immutable normalized log schema.
    """

    timestamp: str
    level: str
    message: str

    schema_version: str
    service: str
    environment: str
    node: str
    version: str

    correlation_id: Optional[str]
    trace_id: Optional[str]
    request_id: Optional[str]

    namespace: Optional[str]
    component: Optional[str]

    tags: Tuple[str, ...]

    payload: Mapping[str, Any]
    exception: Optional[Mapping[str, Any]]

    metadata: Mapping[str, Any]


# ============================================================
# SERIALIZER PROTOCOL
# ============================================================


class Serializer(Protocol):

    def serialize(self, record: LogRecord) -> str:
        ...


# ============================================================
# JSON SERIALIZER
# ============================================================


class JsonSerializer:
    """
    Deterministic JSON serializer.

    Optimized for:
    - stable output
    - low allocations
    - transport safety
    """

    __slots__ = ()

    def serialize(self, record: LogRecord) -> str:

        return json.dumps(
            asdict(record),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
            default=str,
        )


# ============================================================
# REDACTION ENGINE
# ============================================================


class Redactor:
    """
    Pure payload sanitization engine.

    NO SIDE EFFECTS.
    """

    __slots__ = ("_policy",)

    def __init__(self, policy: Optional[RedactionPolicy] = None):
        self._policy = policy or RedactionPolicy()

    def sanitize(self, value: Any) -> Any:

        if not self._policy.enabled:
            return value

        return self._sanitize_recursive(value)

    def _sanitize_recursive(self, value: Any) -> Any:

        if isinstance(value, Mapping):

            sanitized: Dict[str, Any] = {}

            for key, val in value.items():

                normalized_key = key.lower()

                if normalized_key in self._policy.secret_fields:
                    sanitized[key] = self._policy.replacement
                    continue

                if self._policy.recursive:
                    sanitized[key] = self._sanitize_recursive(val)
                else:
                    sanitized[key] = val

            return sanitized

        if isinstance(value, (list, tuple, set, frozenset)):

            return [
                self._sanitize_recursive(v)
                for v in value
            ]

        return value


# ============================================================
# EXCEPTION FORMATTER
# ============================================================


class ExceptionFormatter:
    """
    Deterministic exception normalization.
    """

    __slots__ = ()

    @staticmethod
    def normalize(exc: BaseException) -> Dict[str, Any]:

        return {
            "type": exc.__class__.__name__,
            "message": str(exc),
            "traceback": traceback.format_exception_only(
                type(exc),
                exc,
            ),
        }


# ============================================================
# MAIN FORMATTER
# ============================================================


class LogFormatter:
    """
    Institutional-grade immutable formatter pipeline.

    Features:
    - deterministic normalization
    - immutable outputs
    - correlation support
    - schema versioning
    - payload sanitization
    - safe serialization
    - low-overhead formatting
    """

    __slots__ = (
        "_redactor",
        "_serializer",
    )

    def __init__(
        self,
        *,
        redaction_policy: Optional[RedactionPolicy] = None,
        serializer: Optional[Serializer] = None,
    ) -> None:

        self._redactor = Redactor(redaction_policy)
        self._serializer = serializer or JsonSerializer()

    # ========================================================
    # MAIN API
    # ========================================================

    def create_record(
        self,
        *,
        level: LogLevel,
        message: str,
        context: LogContext,
        payload: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        exception: Optional[BaseException] = None,
    ) -> LogRecord:
        """
        Pure immutable record builder.
        """

        safe_payload = self._redactor.sanitize(payload or {})
        safe_metadata = self._redactor.sanitize(metadata or {})

        normalized_exception = None

        if exception is not None:
            normalized_exception = ExceptionFormatter.normalize(
                exception
            )

        return LogRecord(
            timestamp=self._utc_timestamp(),
            level=level.value,
            message=self._normalize_message(message),

            schema_version=SCHEMA_VERSION,

            service=context.service,
            environment=context.environment,
            node=context.node,
            version=context.version,

            correlation_id=context.correlation_id,
            trace_id=context.trace_id,
            request_id=context.request_id,

            namespace=context.namespace,
            component=context.component,

            tags=context.tags,

            payload=safe_payload,
            exception=normalized_exception,
            metadata=safe_metadata,
        )

    def serialize(self, record: LogRecord) -> str:
        """
        Transport-safe serialization.

        No I/O performed here.
        """

        return self._serializer.serialize(record)

    # ========================================================
    # INTERNAL HELPERS
    # ========================================================

    @staticmethod
    def _utc_timestamp() -> str:
        """
        RFC3339 UTC timestamp.
        """

        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _normalize_message(message: str) -> str:
        """
        Lightweight deterministic message normalization.
        """

        return " ".join(message.strip().split())


# ============================================================
# FACTORY HELPERS
# ============================================================


def create_correlation_id() -> str:
    """
    Collision-resistant correlation ID.

    Stateless + thread-safe.
    """

    return uuid.uuid4().hex


def create_trace_id() -> str:
    """
    Lightweight trace ID helper.

    Avoids telemetry ownership.
    Only generates IDs.
    """

    return uuid.uuid4().hex


# ============================================================
# GLOBAL FORMATTER
# ============================================================

default_formatter = LogFormatter()
