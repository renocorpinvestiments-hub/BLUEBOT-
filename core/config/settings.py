# core/config/settings.py

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import FrozenSet, Mapping, Optional, Tuple


# ============================================================
# BASE
# ============================================================

@dataclass(frozen=True, slots=True)
class BaseConfig:
    """
    Immutable configuration base.

    Design goals:
    - hashable
    - immutable
    - lightweight
    - deterministic
    - runtime-safe
    """

    def as_dict(self) -> dict:
        """
        Safe shallow export for observability/debugging.

        NOTE:
        - no mutation risk
        - deterministic output
        """
        return {
            k: getattr(self, k)
            for k in self.__slots__
        }


# ============================================================
# RUNTIME
# ============================================================

@dataclass(frozen=True, slots=True)
class RuntimeConfig(BaseConfig):
    app_name: str = "renocorp"
    environment: str = "production"

    debug: bool = False
    testing: bool = False

    timezone: str = "UTC"

    max_concurrency: int = 1000
    shutdown_timeout: float = 30.0

    enable_uvloop: bool = True
    enable_gc_optimization: bool = True

    boot_validation: bool = True
    strict_mode: bool = True


# ============================================================
# OBSERVABILITY
# ============================================================

@dataclass(frozen=True, slots=True)
class ObservabilityConfig(BaseConfig):
    service_name: str = "renocorp"

    log_level: str = "INFO"

    metrics_enabled: bool = True
    tracing_enabled: bool = True
    structured_logging: bool = True

    metrics_interval: float = 15.0

    slow_task_threshold: float = 1.0
    queue_warning_threshold: int = 1000

    emit_runtime_metrics: bool = True
    emit_system_metrics: bool = True


# ============================================================
# RESILIENCE
# ============================================================

@dataclass(frozen=True, slots=True)
class ResilienceConfig(BaseConfig):
    retry_attempts: int = 3

    retry_base_delay: float = 0.5
    retry_max_delay: float = 30.0

    circuit_breaker_enabled: bool = True

    circuit_failure_threshold: int = 5
    circuit_recovery_timeout: float = 30.0

    timeout_default: float = 30.0

    bulkhead_enabled: bool = True
    bulkhead_limit: int = 100


# ============================================================
# QUEUE
# ============================================================

@dataclass(frozen=True, slots=True)
class QueueConfig(BaseConfig):
    max_workers: int = 250

    max_queue_size: int = 100_000

    dispatcher_batch_size: int = 100

    publish_timeout: float = 5.0
    processing_timeout: float = 60.0

    retry_failed_messages: bool = True
    max_retry_attempts: int = 5

    enable_backpressure: bool = True

    dead_letter_enabled: bool = True
    dead_letter_max_age: float = 86400.0

    idle_sleep_interval: float = 0.001


# ============================================================
# SECURITY
# ============================================================

@dataclass(frozen=True, slots=True)
class SecurityConfig(BaseConfig):
    jwt_algorithm: str = "HS256"

    token_ttl_seconds: int = 3600

    enforce_authentication: bool = True
    enforce_authorization: bool = True

    allow_unsigned_internal_events: bool = False

    max_clock_skew_seconds: int = 30

    secret_rotation_enabled: bool = True

    audit_failures: bool = True

    trusted_issuers: Tuple[str, ...] = field(default_factory=tuple)
    trusted_audiences: Tuple[str, ...] = field(default_factory=tuple)


# ============================================================
# DATABASE
# ============================================================

@dataclass(frozen=True, slots=True)
class DatabaseConfig(BaseConfig):
    driver: str = "postgresql+asyncpg"

    host: str = "localhost"
    port: int = 5432

    database: str = "renocorp"

    username: str = "postgres"

    pool_min_size: int = 5
    pool_max_size: int = 50

    pool_timeout: float = 30.0
    connection_timeout: float = 10.0

    idle_recycle_seconds: float = 1800.0

    echo_queries: bool = False

    enable_pool_pre_ping: bool = True

    enable_statement_cache: bool = True

    max_transaction_retries: int = 3

    read_replicas: Tuple[str, ...] = field(default_factory=tuple)


# ============================================================
# DISCOVERY
# ============================================================

@dataclass(frozen=True, slots=True)
class DiscoveryConfig(BaseConfig):
    enabled: bool = True

    scan_interval: float = 30.0

    recursive_scan: bool = True

    fail_on_duplicate_module: bool = True

    auto_register: bool = True

    watched_paths: Tuple[Path, ...] = field(default_factory=tuple)


# ============================================================
# LIFECYCLE
# ============================================================

@dataclass(frozen=True, slots=True)
class LifecycleConfig(BaseConfig):
    startup_timeout: float = 60.0

    shutdown_timeout: float = 60.0

    healthcheck_interval: float = 15.0

    readiness_timeout: float = 30.0

    fail_fast: bool = True

    parallel_startup: bool = True


# ============================================================
# EVENT BUS
# ============================================================

@dataclass(frozen=True, slots=True)
class EventConfig(BaseConfig):
    wildcard_subscriptions: bool = True

    max_subscribers_per_event: int = 1000

    event_deduplication: bool = True

    event_ttl_seconds: float = 3600.0

    emit_internal_metrics: bool = True

    strict_event_naming: bool = True


# ============================================================
# CACHE
# ============================================================

@dataclass(frozen=True, slots=True)
class CacheConfig(BaseConfig):
    enabled: bool = True

    backend: str = "memory"

    default_ttl: float = 300.0

    max_entries: int = 100_000

    cleanup_interval: float = 60.0

    compression_enabled: bool = False


# ============================================================
# ROOT APP CONFIG
# ============================================================

@dataclass(frozen=True, slots=True)
class AppConfig(BaseConfig):
    """
    Root immutable application configuration.

    This object is intended to be:
    - loaded once
    - validated once
    - injected into the kernel/runtime
    - never mutated afterward
    """

    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    observability: ObservabilityConfig = field(
        default_factory=ObservabilityConfig
    )

    resilience: ResilienceConfig = field(
        default_factory=ResilienceConfig
    )

    queue: QueueConfig = field(default_factory=QueueConfig)

    security: SecurityConfig = field(
        default_factory=SecurityConfig
    )

    database: DatabaseConfig = field(
        default_factory=DatabaseConfig
    )

    discovery: DiscoveryConfig = field(
        default_factory=DiscoveryConfig
    )

    lifecycle: LifecycleConfig = field(
        default_factory=LifecycleConfig
    )

    events: EventConfig = field(default_factory=EventConfig)

    cache: CacheConfig = field(default_factory=CacheConfig)

    metadata: Mapping[str, str] = field(default_factory=dict)

    enabled_features: FrozenSet[str] = field(
        default_factory=frozenset
    )

    schema_version: int = 1
