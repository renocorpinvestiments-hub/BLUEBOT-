# ============================================================
# core/cache/models.py
# ============================================================
# Institutional Cache Data Models
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# Strict deterministic cache schemas.
#
# THIS FILE DOES:
# - define cache structures
# - enforce schema consistency
# - validate serialized state
# - standardize cache contracts
# - protect compatibility
# - support idempotent restoration
#
# THIS FILE DOES NOT:
# - perform IO
# - manage cache orchestration
# - implement persistence logic
# - handle business workflows
#
# Those responsibilities belong to:
# - cache/manager.py
# - cache/backend.py
#
# DESIGN PRINCIPLES
# ------------------------------------------------------------
# - immutable-like structures
# - deterministic serialization
# - schema version safety
# - future compatibility
# - corruption resistance
# - low-overhead validation
#
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ============================================================
# CONSTANTS
# ============================================================

CACHE_SCHEMA_VERSION = 1


# ============================================================
# BASE CACHE ENTRY
# ============================================================

@dataclass(slots=True)
class CacheEntry:
    """
    Institutional cache entry schema.

    Represents:
    --------------------------------------------------------
    - runtime state
    - validated configs
    - manifests
    - snapshots
    - telemetry windows
    - discovery metadata

    Design Goals:
    --------------------------------------------------------
    - deterministic structure
    - serialization safety
    - schema compatibility
    - corruption detection
    - idempotent restoration
    """

    # --------------------------------------------------------
    # CORE IDENTITY
    # --------------------------------------------------------

    key: str
    namespace: str

    # --------------------------------------------------------
    # PAYLOAD
    # --------------------------------------------------------

    value: Any

    # --------------------------------------------------------
    # VERSIONING
    # --------------------------------------------------------

    version: int = CACHE_SCHEMA_VERSION

    # --------------------------------------------------------
    # TIMING
    # --------------------------------------------------------

    timestamp: int = 0
    ttl: int = 0

    # --------------------------------------------------------
    # INTEGRITY
    # --------------------------------------------------------

    checksum: Optional[str] = None

    # --------------------------------------------------------
    # METADATA
    # --------------------------------------------------------

    metadata: Dict[str, Any] = field(
        default_factory=dict,
    )

    # ========================================================
    # VALIDATION
    # ========================================================

    def validate(self) -> None:
        """
        Lightweight structural validation.
        """

        if not self.key:
            raise ValueError(
                "CacheEntry.key cannot be empty."
            )

        if not self.namespace:
            raise ValueError(
                "CacheEntry.namespace cannot be empty."
            )

        if self.version <= 0:
            raise ValueError(
                "CacheEntry.version must be positive."
            )

        if self.ttl < 0:
            raise ValueError(
                "CacheEntry.ttl cannot be negative."
            )

        if self.timestamp < 0:
            raise ValueError(
                "CacheEntry.timestamp cannot be negative."
            )

    # ========================================================
    # SERIALIZATION
    # ========================================================

    def to_dict(self) -> Dict[str, Any]:
        """
        Deterministic serialization contract.
        """

        self.validate()

        return {
            "key": self.key,
            "namespace": self.namespace,
            "value": self.value,
            "version": self.version,
            "timestamp": self.timestamp,
            "ttl": self.ttl,
            "checksum": self.checksum,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Dict[str, Any],
    ) -> "CacheEntry":
        """
        Safe deserialization contract.
        """

        entry = cls(
            key=payload["key"],
            namespace=payload["namespace"],
            value=payload.get("value"),
            version=payload.get(
                "version",
                CACHE_SCHEMA_VERSION,
            ),
            timestamp=payload.get(
                "timestamp",
                0,
            ),
            ttl=payload.get(
                "ttl",
                0,
            ),
            checksum=payload.get(
                "checksum",
            ),
            metadata=payload.get(
                "metadata",
                {},
            ),
        )

        entry.validate()

        return entry


# ============================================================
# SNAPSHOT METADATA
# ============================================================

@dataclass(slots=True)
class SnapshotMetadata:
    """
    Snapshot restoration metadata.

    Used for:
    --------------------------------------------------------
    - crash recovery
    - runtime restoration
    - startup validation
    - migration compatibility
    """

    snapshot_id: str
    namespace: str

    created_at: int

    entry_count: int = 0

    checksum: Optional[str] = None

    version: int = CACHE_SCHEMA_VERSION

    metadata: Dict[str, Any] = field(
        default_factory=dict,
    )

    # ========================================================
    # VALIDATION
    # ========================================================

    def validate(self) -> None:

        if not self.snapshot_id:
            raise ValueError(
                "SnapshotMetadata.snapshot_id missing."
            )

        if not self.namespace:
            raise ValueError(
                "SnapshotMetadata.namespace missing."
            )

        if self.created_at < 0:
            raise ValueError(
                "SnapshotMetadata.created_at invalid."
            )

        if self.entry_count < 0:
            raise ValueError(
                "SnapshotMetadata.entry_count invalid."
            )

    # ========================================================
    # SERIALIZATION
    # ========================================================

    def to_dict(self) -> Dict[str, Any]:

        self.validate()

        return {
            "snapshot_id": self.snapshot_id,
            "namespace": self.namespace,
            "created_at": self.created_at,
            "entry_count": self.entry_count,
            "checksum": self.checksum,
            "version": self.version,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Dict[str, Any],
    ) -> "SnapshotMetadata":

        metadata = cls(
            snapshot_id=payload["snapshot_id"],
            namespace=payload["namespace"],
            created_at=payload["created_at"],
            entry_count=payload.get(
                "entry_count",
                0,
            ),
            checksum=payload.get(
                "checksum",
            ),
            version=payload.get(
                "version",
                CACHE_SCHEMA_VERSION,
            ),
            metadata=payload.get(
                "metadata",
                {},
            ),
        )

        metadata.validate()

        return metadata


# ============================================================
# RUNTIME CACHE STATE
# ============================================================

@dataclass(slots=True)
class RuntimeCacheState:
    """
    Lightweight runtime observability snapshot.

    Used for:
    --------------------------------------------------------
    - diagnostics
    - restoration
    - runtime coordination
    """

    namespaces: int = 0

    memory_entries: int = 0

    dirty_entries: int = 0

    started: bool = False

    metadata: Dict[str, Any] = field(
        default_factory=dict,
    )

    # ========================================================
    # SERIALIZATION
    # ========================================================

    def to_dict(self) -> Dict[str, Any]:

        return {
            "namespaces": self.namespaces,
            "memory_entries": self.memory_entries,
            "dirty_entries": self.dirty_entries,
            "started": self.started,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Dict[str, Any],
    ) -> "RuntimeCacheState":

        return cls(
            namespaces=payload.get(
                "namespaces",
                0,
            ),
            memory_entries=payload.get(
                "memory_entries",
                0,
            ),
            dirty_entries=payload.get(
                "dirty_entries",
                0,
            ),
            started=payload.get(
                "started",
                False,
            ),
            metadata=payload.get(
                "metadata",
                {},
            ),
        )
