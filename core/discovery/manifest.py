"""
core/discovery/manifest.py

RENOCORP Institutional Manifest System
======================================

Purpose
-------
Defines immutable plugin/module metadata contracts.

This module ONLY handles:
- metadata schemas
- compatibility contracts
- capability declarations
- manifest validation
- dependency safety
- schema normalization

This module DOES NOT:
- discover modules
- import modules
- execute modules
- manage runtime state
- orchestrate services

Design Goals
------------
- immutable
- deterministic
- idempotent
- async-safe
- future-proof
- schema-extensible
- distributed-ready
- low-overhead
- runtime-safe

Compatible With
----------------
- AI systems
- trading systems
- web applications
- SaaS platforms
- event-driven runtimes
- distributed workers
- plugin ecosystems
- microservices
- automation systems

Future Compatible With
-----------------------
- signed plugins
- remote manifests
- marketplace registries
- capability negotiation
- WASM modules
- distributed runtime federation
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Dict,
    FrozenSet,
    Mapping,
    Optional,
    Tuple,
)

# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTS
# ============================================================

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-[\da-z\-]+(?:\.[\da-z\-]+)*)?"
    r"(?:\+[\da-z\-]+(?:\.[\da-z\-]+)*)?$",
    re.IGNORECASE,
)

SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})

DEFAULT_RUNTIME_VERSION = "1.0"

# ============================================================
# EXCEPTIONS
# ============================================================


class ManifestError(Exception):
    """
    Base manifest exception.
    """


class ManifestValidationError(ManifestError):
    """
    Raised when manifest validation fails.
    """


class CompatibilityError(ManifestError):
    """
    Raised for runtime compatibility failures.
    """


# ============================================================
# CAPABILITIES
# ============================================================


class Capability(str, Enum):
    """
    Standardized capability contract.

    Keeps capability negotiation deterministic.
    """

    WORKER = "WORKER"
    EVENT_CONSUMER = "EVENT_CONSUMER"
    EVENT_PRODUCER = "EVENT_PRODUCER"
    QUEUE_HANDLER = "QUEUE_HANDLER"
    TELEMETRY_PROVIDER = "TELEMETRY_PROVIDER"
    STRATEGY = "STRATEGY"
    API_PROVIDER = "API_PROVIDER"
    STORAGE_PROVIDER = "STORAGE_PROVIDER"
    AUTH_PROVIDER = "AUTH_PROVIDER"
    ANALYTICS_PROVIDER = "ANALYTICS_PROVIDER"
    MODEL_PROVIDER = "MODEL_PROVIDER"
    EXECUTION_ENGINE = "EXECUTION_ENGINE"


# ============================================================
# PERMISSIONS
# ============================================================


class Permission(str, Enum):
    """
    Restricted runtime permissions.

    Prevents uncontrolled expansion.
    """

    FILESYSTEM_READ = "FILESYSTEM_READ"
    FILESYSTEM_WRITE = "FILESYSTEM_WRITE"
    NETWORK_ACCESS = "NETWORK_ACCESS"
    ENV_ACCESS = "ENV_ACCESS"
    PROCESS_EXECUTION = "PROCESS_EXECUTION"
    MEMORY_SHARED = "MEMORY_SHARED"


# ============================================================
# COMPATIBILITY
# ============================================================


@dataclass(frozen=True, slots=True)
class CompatibilitySpec:
    """
    Runtime compatibility contract.
    """

    minimum_runtime_version: str = DEFAULT_RUNTIME_VERSION
    maximum_runtime_version: Optional[str] = None
    supported_platforms: FrozenSet[str] = frozenset()
    feature_flags: FrozenSet[str] = frozenset()


# ============================================================
# RUNTIME REQUIREMENTS
# ============================================================


@dataclass(frozen=True, slots=True)
class RuntimeRequirements:
    """
    Runtime resource requirements.
    """

    minimum_python: str = "3.11"
    requires_asyncio: bool = True
    requires_network: bool = False
    requires_filesystem: bool = False
    required_packages: FrozenSet[str] = frozenset()


# ============================================================
# MODULE MANIFEST
# ============================================================


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    """
    Immutable module metadata contract.

    Institutional Design:
    - immutable
    - deterministic
    - serializable
    - hashable
    - schema-extensible
    """

    name: str
    version: str

    description: str = ""
    author: str = ""

    schema_version: str = "1.0"

    capabilities: FrozenSet[Capability] = frozenset()

    permissions: FrozenSet[Permission] = frozenset()

    dependencies: FrozenSet[str] = frozenset()

    optional_dependencies: FrozenSet[str] = frozenset()

    entrypoints: Mapping[str, str] = field(
        default_factory=dict
    )

    compatibility: CompatibilitySpec = field(
        default_factory=CompatibilitySpec
    )

    runtime_requirements: RuntimeRequirements = field(
        default_factory=RuntimeRequirements
    )

    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )

    checksum: str = ""

    signature: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Deterministic serialization.
        """

        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "schema_version": self.schema_version,
            "capabilities": sorted(
                c.value for c in self.capabilities
            ),
            "permissions": sorted(
                p.value for p in self.permissions
            ),
            "dependencies": sorted(self.dependencies),
            "optional_dependencies": sorted(
                self.optional_dependencies
            ),
            "entrypoints": dict(self.entrypoints),
            "compatibility": {
                "minimum_runtime_version":
                    self.compatibility.minimum_runtime_version,
                "maximum_runtime_version":
                    self.compatibility.maximum_runtime_version,
                "supported_platforms":
                    sorted(
                        self.compatibility.supported_platforms
                    ),
                "feature_flags":
                    sorted(
                        self.compatibility.feature_flags
                    ),
            },
            "runtime_requirements": {
                "minimum_python":
                    self.runtime_requirements.minimum_python,
                "requires_asyncio":
                    self.runtime_requirements.requires_asyncio,
                "requires_network":
                    self.runtime_requirements.requires_network,
                "requires_filesystem":
                    self.runtime_requirements.requires_filesystem,
                "required_packages":
                    sorted(
                        self.runtime_requirements.required_packages
                    ),
            },
            "metadata": dict(self.metadata),
            "checksum": self.checksum,
            "signature": self.signature,
        }

    def stable_hash(self) -> str:
        """
        Generate deterministic manifest hash.
        """

        encoded = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        return hashlib.sha256(encoded).hexdigest()


# ============================================================
# VALIDATION RESULT
# ============================================================


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """
    Immutable validation result.
    """

    valid: bool
    errors: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()


# ============================================================
# MANIFEST VALIDATOR
# ============================================================


class ManifestValidator:
    """
    Institutional-grade manifest validator.

    Responsibilities:
    - schema validation
    - semantic version validation
    - compatibility validation
    - dependency safety
    - permission validation
    - deterministic validation

    Non-Responsibilities:
    - runtime loading
    - plugin execution
    - discovery scanning
    """

    def __init__(
        self,
        *,
        runtime_version: str = DEFAULT_RUNTIME_VERSION,
        strict_mode: bool = True,
        allowed_permissions: Optional[
            FrozenSet[Permission]
        ] = None,
    ) -> None:

        self._runtime_version = runtime_version

        self._strict_mode = strict_mode

        self._allowed_permissions = (
            allowed_permissions
            or frozenset(Permission)
        )

        self._cache: Dict[str, ValidationResult] = {}

    # ========================================================
    # PUBLIC API
    # ========================================================

    def validate(
        self,
        manifest: ModuleManifest,
    ) -> ValidationResult:
        """
        Deterministic manifest validation.

        Idempotent:
        repeated validation produces identical results.
        """

        manifest_hash = manifest.stable_hash()

        cached = self._cache.get(manifest_hash)

        if cached:
            return cached

        errors = []
        warnings = []

        # ====================================================
        # REQUIRED FIELDS
        # ====================================================

        if not manifest.name.strip():
            errors.append(
                "Manifest name cannot be empty."
            )

        if not manifest.version.strip():
            errors.append(
                "Manifest version cannot be empty."
            )

        # ====================================================
        # SEMVER
        # ====================================================

        if not self._is_valid_semver(
            manifest.version
        ):
            errors.append(
                f"Invalid semantic version: "
                f"{manifest.version}"
            )

        # ====================================================
        # SCHEMA VERSION
        # ====================================================

        if (
            manifest.schema_version
            not in SUPPORTED_SCHEMA_VERSIONS
        ):
            errors.append(
                f"Unsupported schema version: "
                f"{manifest.schema_version}"
            )

        # ====================================================
        # PERMISSIONS
        # ====================================================

        for permission in manifest.permissions:

            if permission not in self._allowed_permissions:
                errors.append(
                    f"Permission not allowed: "
                    f"{permission.value}"
                )

        # ====================================================
        # DEPENDENCY VALIDATION
        # ====================================================

        duplicate_dependencies = (
            manifest.dependencies
            & manifest.optional_dependencies
        )

        if duplicate_dependencies:
            errors.append(
                "Duplicate dependency definitions: "
                + ", ".join(
                    sorted(duplicate_dependencies)
                )
            )

        # ====================================================
        # ENTRYPOINT VALIDATION
        # ====================================================

        for key, value in (
            manifest.entrypoints.items()
        ):

            if not key.strip():
                errors.append(
                    "Entrypoint key cannot be empty."
                )

            if ":" not in value:
                errors.append(
                    f"Invalid entrypoint format: "
                    f"{value}"
                )

        # ====================================================
        # COMPATIBILITY VALIDATION
        # ====================================================

        compatibility_error = (
            self._validate_compatibility(
                manifest.compatibility
            )
        )

        if compatibility_error:
            errors.append(compatibility_error)

        # ====================================================
        # CHECKSUM VALIDATION
        # ====================================================

        if (
            self._strict_mode
            and manifest.checksum
            and len(manifest.checksum) < 32
        ):
            errors.append(
                "Checksum length invalid."
            )

        # ====================================================
        # WARNINGS
        # ====================================================

        if not manifest.capabilities:
            warnings.append(
                "Manifest exposes no capabilities."
            )

        if not manifest.entrypoints:
            warnings.append(
                "Manifest has no entrypoints."
            )

        result = ValidationResult(
            valid=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

        self._cache[manifest_hash] = result

        return result

    # ========================================================
    # STRICT VALIDATION
    # ========================================================

    def validate_or_raise(
        self,
        manifest: ModuleManifest,
    ) -> None:
        """
        Strict validation mode.
        """

        result = self.validate(manifest)

        if not result.valid:

            raise ManifestValidationError(
                "\n".join(result.errors)
            )

    # ========================================================
    # INTERNALS
    # ========================================================

    def _validate_compatibility(
        self,
        compatibility: CompatibilitySpec,
    ) -> Optional[str]:
        """
        Validate runtime compatibility.
        """

        if not self._is_valid_semver(
            compatibility.minimum_runtime_version
        ):
            return (
                "Invalid minimum runtime version."
            )

        if (
            compatibility.maximum_runtime_version
            and not self._is_valid_semver(
                compatibility.maximum_runtime_version
            )
        ):
            return (
                "Invalid maximum runtime version."
            )

        return None

    @staticmethod
    def _is_valid_semver(
        version: str,
    ) -> bool:
        """
        Fast semantic version validation.
        """

        return bool(
            SEMVER_PATTERN.match(version)
        )

    # ========================================================
    # CACHE CONTROL
    # ========================================================

    def clear_cache(self) -> None:
        """
        Clear validation cache.
        """

        self._cache.clear()

    # ========================================================
    # SNAPSHOT
    # ========================================================

    def snapshot(self) -> Dict[str, Any]:
        """
        Lightweight validator state snapshot.
        """

        return {
            "runtime_version":
                self._runtime_version,
            "strict_mode":
                self._strict_mode,
            "cache_size":
                len(self._cache),
        }

    # ========================================================
    # CLEANUP
    # ========================================================

    async def shutdown(self) -> None:
        """
        Graceful validator cleanup.
        """

        self.clear_cache()

        logger.info(
            "ManifestValidator shutdown complete."
        )
