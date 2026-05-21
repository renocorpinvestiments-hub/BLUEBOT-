# core/config/loader.py

from __future__ import annotations

import json
import os
import time

from dataclasses import asdict
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Mapping, MutableMapping, Optional

from core.config.schema import (
    ConfigSchema,
    ValidationResult,
)
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
# OPTIONAL YAML SUPPORT
# ============================================================

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


# ============================================================
# ERRORS
# ============================================================

class ConfigLoadError(Exception):
    """
    Raised when configuration loading fails.
    """

    pass


# ============================================================
# PROVIDER CONTRACT
# ============================================================

class ConfigProvider:
    """
    Abstract configuration provider.

    Providers must:
    - be deterministic
    - avoid side effects
    - avoid global mutation
    - return plain dictionaries only
    """

    __slots__ = ()

    async def load(self) -> Mapping[str, Any]:
        raise NotImplementedError


# ============================================================
# ENV PROVIDER
# ============================================================

class EnvProvider(ConfigProvider):

    __slots__ = ("_prefix",)

    def __init__(
        self,
        prefix: str = "RENOCORP_",
    ) -> None:
        self._prefix = prefix

    async def load(self) -> Mapping[str, Any]:
        result: Dict[str, Any] = {}

        for key, value in os.environ.items():
            if not key.startswith(self._prefix):
                continue

            normalized = (
                key[len(self._prefix):]
                .lower()
            )

            result[normalized] = value

        return MappingProxyType(result)


# ============================================================
# FILE PROVIDER
# ============================================================

class FileProvider(ConfigProvider):

    __slots__ = ("_path",)

    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self._path = Path(path)

    async def load(self) -> Mapping[str, Any]:
        if not self._path.exists():
            raise ConfigLoadError(
                f"configuration file not found: {self._path}"
            )

        suffix = self._path.suffix.lower()

        try:
            if suffix == ".json":
                return MappingProxyType(
                    self._load_json()
                )

            if suffix in {".yaml", ".yml"}:
                return MappingProxyType(
                    self._load_yaml()
                )

        except Exception as exc:
            raise ConfigLoadError(
                f"failed to load configuration file: {exc}"
            ) from exc

        raise ConfigLoadError(
            f"unsupported config format: {suffix}"
        )

    def _load_json(self) -> Dict[str, Any]:
        with self._path.open(
            "r",
            encoding="utf-8",
        ) as fh:
            data = json.load(fh)

        if not isinstance(data, dict):
            raise ConfigLoadError(
                "json configuration root must be object"
            )

        return data

    def _load_yaml(self) -> Dict[str, Any]:
        if yaml is None:
            raise ConfigLoadError(
                "PyYAML is not installed"
            )

        with self._path.open(
            "r",
            encoding="utf-8",
        ) as fh:
            data = yaml.safe_load(fh)

        if not isinstance(data, dict):
            raise ConfigLoadError(
                "yaml configuration root must be object"
            )

        return data


# ============================================================
# CONFIG LOADER
# ============================================================

class ConfigLoader:
    """
    Institutional-grade configuration loader.

    Responsibilities:
    - assemble configuration
    - compose providers
    - freeze runtime configuration
    - validate before runtime boot

    Non-responsibilities:
    - runtime logic
    - service initialization
    - dependency injection
    - observability emission
    """

    __slots__ = (
        "_providers",
        "_schema",
    )

    def __init__(
        self,
        *providers: ConfigProvider,
        schema: Optional[ConfigSchema] = None,
    ) -> None:
        self._providers = providers
        self._schema = schema or ConfigSchema()

    # ========================================================
    # PUBLIC API
    # ========================================================

    async def load(self) -> AppConfig:
        """
        Deterministic configuration load.

        Properties:
        - async-safe
        - idempotent
        - immutable
        - side-effect free
        - scalable
        """

        merged: Dict[str, Any] = {}

        for provider in self._providers:
            loaded = await provider.load()

            self._deep_merge(
                merged,
                dict(loaded),
            )

        config = self._build_config(merged)

        validation: ValidationResult = (
            self._schema.validate(config)
        )

        return validation.validated_config

    # ========================================================
    # BUILDERS
    # ========================================================

    def _build_config(
        self,
        raw: Mapping[str, Any],
    ) -> AppConfig:

        runtime = RuntimeConfig(
            **raw.get("runtime", {})
        )

        observability = ObservabilityConfig(
            **raw.get("observability", {})
        )

        resilience = ResilienceConfig(
            **raw.get("resilience", {})
        )

        queue = QueueConfig(
            **raw.get("queue", {})
        )

        security = SecurityConfig(
            **raw.get("security", {})
        )

        database = DatabaseConfig(
            **raw.get("database", {})
        )

        discovery = DiscoveryConfig(
            **raw.get("discovery", {})
        )

        lifecycle = LifecycleConfig(
            **raw.get("lifecycle", {})
        )

        events = EventConfig(
            **raw.get("events", {})
        )

        cache = CacheConfig(
            **raw.get("cache", {})
        )

        return AppConfig(
            runtime=runtime,
            observability=observability,
            resilience=resilience,
            queue=queue,
            security=security,
            database=database,
            discovery=discovery,
            lifecycle=lifecycle,
            events=events,
            cache=cache,
            metadata=raw.get("metadata", {}),
            enabled_features=frozenset(
                raw.get(
                    "enabled_features",
                    [],
                )
            ),
            schema_version=raw.get(
                "schema_version",
                1,
            ),
        )

    # ========================================================
    # MERGE
    # ========================================================

    def _deep_merge(
        self,
        target: MutableMapping[str, Any],
        source: Mapping[str, Any],
    ) -> None:
        """
        Deterministic recursive merge.

        Priority:
        later providers override earlier providers.
        """

        for key, value in source.items():

            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                self._deep_merge(
                    target[key],
                    value,
                )
                continue

            target[key] = value

    # ========================================================
    # SNAPSHOT
    # ========================================================

    @staticmethod
    def snapshot(
        config: AppConfig,
    ) -> Mapping[str, Any]:
        """
        Immutable configuration snapshot.

        Useful for:
        - observability
        - deployment diffing
        - runtime diagnostics
        """

        return MappingProxyType(
            asdict(config)
        )


# ============================================================
# FACTORY HELPERS
# ============================================================

def create_default_loader(
    config_path: Optional[str | Path] = None,
) -> ConfigLoader:
    """
    Standard production loader factory.

    Resolution order:
    defaults
    -> file
    -> environment
    """

    providers: list[ConfigProvider] = []

    if config_path is not None:
        providers.append(
            FileProvider(config_path)
        )

    providers.append(
        EnvProvider()
    )

    return ConfigLoader(*providers)
