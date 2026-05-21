# core/config/schema.py

from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Callable, Iterable, List, Sequence

from core.config.settings import (
    AppConfig,
    CacheConfig,
    DatabaseConfig,
    DiscoveryConfig,
    EventConfig,
    LifecycleConfig,
    ObservabilityConfig,
    QueueConfig,
    ResilienceConfig,
    RuntimeConfig,
    SecurityConfig,
)


# ============================================================
# CONSTANTS
# ============================================================

SCHEMA_VERSION = 1

_VALID_ENVIRONMENTS = frozenset({
    "development",
    "testing",
    "staging",
    "production",
})

_VALID_LOG_LEVELS = frozenset({
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
})

_VALID_JWT_ALGORITHMS = frozenset({
    "HS256",
    "HS384",
    "HS512",
    "RS256",
    "RS384",
    "RS512",
})

_DRIVER_PATTERN = re.compile(
    r"^[a-zA-Z0-9+._-]+$"
)


# ============================================================
# ERRORS
# ============================================================

class ConfigValidationError(Exception):
    """
    Raised when configuration validation fails.
    """

    pass


# ============================================================
# VALIDATION RESULT
# ============================================================

@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    schema_version: int
    validated_config: AppConfig


# ============================================================
# SCHEMA VALIDATOR
# ============================================================

class ConfigSchema:
    """
    Centralized configuration validation layer.

    Responsibilities:
    - validate typed configuration
    - enforce runtime invariants
    - guarantee deterministic boot validation
    - fail fast on invalid infrastructure state

    Non-responsibilities:
    - loading env vars
    - parsing files
    - mutating configuration
    - dependency injection
    """

    __slots__ = ("_validators",)

    def __init__(self) -> None:
        self._validators: Sequence[
            Callable[[AppConfig], None]
        ] = (
            self._validate_schema_version,
            self._validate_runtime,
            self._validate_observability,
            self._validate_resilience,
            self._validate_queue,
            self._validate_security,
            self._validate_database,
            self._validate_discovery,
            self._validate_lifecycle,
            self._validate_events,
            self._validate_cache,
            self._validate_cross_domain_constraints,
        )

    # ========================================================
    # PUBLIC API
    # ========================================================

    def validate(
        self,
        config: AppConfig,
    ) -> ValidationResult:
        """
        Validate immutable application configuration.

        Properties:
        - deterministic
        - idempotent
        - side-effect free
        - thread-safe
        """

        if not isinstance(config, AppConfig):
            raise ConfigValidationError(
                "config must be an instance of AppConfig"
            )

        for validator in self._validators:
            validator(config)

        return ValidationResult(
            valid=True,
            schema_version=SCHEMA_VERSION,
            validated_config=config,
        )

    # ========================================================
    # RUNTIME
    # ========================================================

    def _validate_runtime(
        self,
        config: AppConfig,
    ) -> None:
        runtime: RuntimeConfig = config.runtime

        self._require_non_empty(
            runtime.app_name,
            "runtime.app_name",
        )

        self._require_in(
            runtime.environment,
            _VALID_ENVIRONMENTS,
            "runtime.environment",
        )

        self._require_positive_int(
            runtime.max_concurrency,
            "runtime.max_concurrency",
        )

        self._require_positive_float(
            runtime.shutdown_timeout,
            "runtime.shutdown_timeout",
        )

        if runtime.debug and runtime.environment == "production":
            raise ConfigValidationError(
                "debug mode cannot be enabled in production"
            )

        if runtime.testing and runtime.environment == "production":
            raise ConfigValidationError(
                "testing mode cannot run in production"
            )

    # ========================================================
    # OBSERVABILITY
    # ========================================================

    def _validate_observability(
        self,
        config: AppConfig,
    ) -> None:
        obs: ObservabilityConfig = config.observability

        self._require_non_empty(
            obs.service_name,
            "observability.service_name",
        )

        self._require_in(
            obs.log_level,
            _VALID_LOG_LEVELS,
            "observability.log_level",
        )

        self._require_positive_float(
            obs.metrics_interval,
            "observability.metrics_interval",
        )

        self._require_positive_float(
            obs.slow_task_threshold,
            "observability.slow_task_threshold",
        )

        self._require_non_negative_int(
            obs.queue_warning_threshold,
            "observability.queue_warning_threshold",
        )

    # ========================================================
    # RESILIENCE
    # ========================================================

    def _validate_resilience(
        self,
        config: AppConfig,
    ) -> None:
        resilience: ResilienceConfig = config.resilience

        self._require_non_negative_int(
            resilience.retry_attempts,
            "resilience.retry_attempts",
        )

        self._require_positive_float(
            resilience.retry_base_delay,
            "resilience.retry_base_delay",
        )

        self._require_positive_float(
            resilience.retry_max_delay,
            "resilience.retry_max_delay",
        )

        if (
            resilience.retry_base_delay
            > resilience.retry_max_delay
        ):
            raise ConfigValidationError(
                "retry_base_delay cannot exceed retry_max_delay"
            )

        self._require_positive_int(
            resilience.circuit_failure_threshold,
            "resilience.circuit_failure_threshold",
        )

        self._require_positive_float(
            resilience.circuit_recovery_timeout,
            "resilience.circuit_recovery_timeout",
        )

        self._require_positive_float(
            resilience.timeout_default,
            "resilience.timeout_default",
        )

    # ========================================================
    # QUEUE
    # ========================================================

    def _validate_queue(
        self,
        config: AppConfig,
    ) -> None:
        queue: QueueConfig = config.queue

        self._require_positive_int(
            queue.max_workers,
            "queue.max_workers",
        )

        self._require_positive_int(
            queue.max_queue_size,
            "queue.max_queue_size",
        )

        self._require_positive_int(
            queue.dispatcher_batch_size,
            "queue.dispatcher_batch_size",
        )

        self._require_positive_float(
            queue.publish_timeout,
            "queue.publish_timeout",
        )

        self._require_positive_float(
            queue.processing_timeout,
            "queue.processing_timeout",
        )

        self._require_non_negative_int(
            queue.max_retry_attempts,
            "queue.max_retry_attempts",
        )

        if (
            queue.dispatcher_batch_size
            > queue.max_queue_size
        ):
            raise ConfigValidationError(
                "dispatcher_batch_size "
                "cannot exceed max_queue_size"
            )

    # ========================================================
    # SECURITY
    # ========================================================

    def _validate_security(
        self,
        config: AppConfig,
    ) -> None:
        security: SecurityConfig = config.security

        self._require_in(
            security.jwt_algorithm,
            _VALID_JWT_ALGORITHMS,
            "security.jwt_algorithm",
        )

        self._require_positive_int(
            security.token_ttl_seconds,
            "security.token_ttl_seconds",
        )

        self._require_non_negative_int(
            security.max_clock_skew_seconds,
            "security.max_clock_skew_seconds",
        )

    # ========================================================
    # DATABASE
    # ========================================================

    def _validate_database(
        self,
        config: AppConfig,
    ) -> None:
        db: DatabaseConfig = config.database

        self._require_non_empty(
            db.driver,
            "database.driver",
        )

        if not _DRIVER_PATTERN.match(db.driver):
            raise ConfigValidationError(
                "database.driver contains invalid characters"
            )

        self._require_non_empty(
            db.host,
            "database.host",
        )

        self._require_port(
            db.port,
            "database.port",
        )

        self._require_non_empty(
            db.database,
            "database.database",
        )

        self._require_non_empty(
            db.username,
            "database.username",
        )

        self._require_positive_int(
            db.pool_min_size,
            "database.pool_min_size",
        )

        self._require_positive_int(
            db.pool_max_size,
            "database.pool_max_size",
        )

        if db.pool_min_size > db.pool_max_size:
            raise ConfigValidationError(
                "database.pool_min_size "
                "cannot exceed pool_max_size"
            )

        self._require_positive_float(
            db.pool_timeout,
            "database.pool_timeout",
        )

        self._require_positive_float(
            db.connection_timeout,
            "database.connection_timeout",
        )

        self._require_non_negative_int(
            db.max_transaction_retries,
            "database.max_transaction_retries",
        )

    # ========================================================
    # DISCOVERY
    # ========================================================

    def _validate_discovery(
        self,
        config: AppConfig,
    ) -> None:
        discovery: DiscoveryConfig = config.discovery

        self._require_positive_float(
            discovery.scan_interval,
            "discovery.scan_interval",
        )

    # ========================================================
    # LIFECYCLE
    # ========================================================

    def _validate_lifecycle(
        self,
        config: AppConfig,
    ) -> None:
        lifecycle: LifecycleConfig = config.lifecycle

        self._require_positive_float(
            lifecycle.startup_timeout,
            "lifecycle.startup_timeout",
        )

        self._require_positive_float(
            lifecycle.shutdown_timeout,
            "lifecycle.shutdown_timeout",
        )

        self._require_positive_float(
            lifecycle.healthcheck_interval,
            "lifecycle.healthcheck_interval",
        )

        self._require_positive_float(
            lifecycle.readiness_timeout,
            "lifecycle.readiness_timeout",
        )

    # ========================================================
    # EVENTS
    # ========================================================

    def _validate_events(
        self,
        config: AppConfig,
    ) -> None:
        events: EventConfig = config.events

        self._require_positive_int(
            events.max_subscribers_per_event,
            "events.max_subscribers_per_event",
        )

        self._require_positive_float(
            events.event_ttl_seconds,
            "events.event_ttl_seconds",
        )

    # ========================================================
    # CACHE
    # ========================================================

    def _validate_cache(
        self,
        config: AppConfig,
    ) -> None:
        cache: CacheConfig = config.cache

        self._require_non_empty(
            cache.backend,
            "cache.backend",
        )

        self._require_positive_float(
            cache.default_ttl,
            "cache.default_ttl",
        )

        self._require_positive_int(
            cache.max_entries,
            "cache.max_entries",
        )

        self._require_positive_float(
            cache.cleanup_interval,
            "cache.cleanup_interval",
        )

    # ========================================================
    # CROSS DOMAIN VALIDATION
    # ========================================================

    def _validate_cross_domain_constraints(
        self,
        config: AppConfig,
    ) -> None:
        if (
            config.queue.max_workers
            > config.runtime.max_concurrency
        ):
            raise ConfigValidationError(
                "queue.max_workers cannot exceed "
                "runtime.max_concurrency"
            )

        if (
            config.database.pool_max_size
            > config.runtime.max_concurrency
        ):
            raise ConfigValidationError(
                "database.pool_max_size cannot exceed "
                "runtime.max_concurrency"
            )

        if (
            config.runtime.strict_mode
            and not config.security.enforce_authentication
        ):
            raise ConfigValidationError(
                "strict_mode requires authentication"
            )

    # ========================================================
    # SCHEMA VERSION
    # ========================================================

    def _validate_schema_version(
        self,
        config: AppConfig,
    ) -> None:
        if config.schema_version != SCHEMA_VERSION:
            raise ConfigValidationError(
                "unsupported configuration schema version"
            )

    # ========================================================
    # VALIDATION HELPERS
    # ========================================================

    @staticmethod
    def _require_non_empty(
        value: str,
        field_name: str,
    ) -> None:
        if not value or not value.strip():
            raise ConfigValidationError(
                f"{field_name} cannot be empty"
            )

    @staticmethod
    def _require_positive_int(
        value: int,
        field_name: str,
    ) -> None:
        if value <= 0:
            raise ConfigValidationError(
                f"{field_name} must be > 0"
            )

    @staticmethod
    def _require_non_negative_int(
        value: int,
        field_name: str,
    ) -> None:
        if value < 0:
            raise ConfigValidationError(
                f"{field_name} must be >= 0"
            )

    @staticmethod
    def _require_positive_float(
        value: float,
        field_name: str,
    ) -> None:
        if value <= 0:
            raise ConfigValidationError(
                f"{field_name} must be > 0"
            )

    @staticmethod
    def _require_port(
        value: int,
        field_name: str,
    ) -> None:
        if value < 1 or value > 65535:
            raise ConfigValidationError(
                f"{field_name} must be between 1 and 65535"
            )

    @staticmethod
    def _require_in(
        value: str,
        allowed: Iterable[str],
        field_name: str,
    ) -> None:
        if value not in allowed:
            raise ConfigValidationError(
                f"{field_name} contains invalid value: {value}"
            )
