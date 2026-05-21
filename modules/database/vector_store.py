# vector_store.py
from __future__ import annotations

import os
import json
import threading
from typing import List, Dict, Any

import numpy as np


class VectorStore:
    """
    Institution-grade vector persistence layer.

    ROLE:
    - Persist vectors + records
    - Load them efficiently
    - Append safely
    - Serve memory to search layer

    BACKEND:
    - NumPy (today)
    - FAISS / ANN (tomorrow)
    """

    def __init__(
        self,
        storage_dir: str = "vector_db",
        vector_dim: int = 384,
    ):
        self.storage_dir = storage_dir
        self.vector_dim = vector_dim

        self.vectors_path = os.path.join(storage_dir, "vectors.npy")
        self.records_path = os.path.join(storage_dir, "records.jsonl")

        self._lock = threading.Lock()

        self._vectors: np.ndarray | None = None
        self._records: List[Dict[str, Any]] | None = None

        os.makedirs(self.storage_dir, exist_ok=True)

    # ======================================================
    # Public API
    # ======================================================

    def load(self) -> None:
        """
        Loads vectors and records into memory.
        Uses memory-mapped arrays when possible.
        """
        with self._lock:
            self._vectors = self._load_vectors()
            self._records = self._load_records()

    def is_empty(self) -> bool:
        """
        Checks if store has any data.
        """
        return (
            self._vectors is None
            or self._records is None
            or len(self._records) == 0
        )

    def add(self, records: List[Dict[str, Any]]) -> None:
        """
        Appends new vector records safely.
        Each record MUST contain:
        - vector: np.ndarray (float32, L2-normalized)
        - text: str
        - metadata: dict
        """
        if not records:
            return

        with self._lock:
            vectors = np.vstack([r["vector"] for r in records])
            self._validate_vectors(vectors)

            self._append_vectors(vectors)
            self._append_records(records)

            # Reload in-memory state
            self._vectors = self._load_vectors()
            self._records = self._load_records()

    def get_store(self) -> Dict[str, Any]:
        """
        Returns store in QueryEngine-compatible format.
        """
        if self._vectors is None or self._records is None:
            self.load()

        return {
            "vectors": self._vectors,
            "records": self._records,
        }

    # ======================================================
    # Internal loaders
    # ======================================================

    def _load_vectors(self) -> np.ndarray:
        if not os.path.exists(self.vectors_path):
            return np.empty((0, self.vector_dim), dtype=np.float32)

        return np.load(self.vectors_path, mmap_mode="r")

    def _load_records(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.records_path):
            return []

        records = []
        with open(self.records_path, "r", encoding="utf-8") as f:
            for line in f:
                records.append(json.loads(line))
        return records

    # ======================================================
    # Internal appenders
    # ======================================================

    def _append_vectors(self, new_vectors: np.ndarray) -> None:
        if not os.path.exists(self.vectors_path):
            np.save(self.vectors_path, new_vectors)
            return

        existing = np.load(self.vectors_path)
        combined = np.vstack([existing, new_vectors])
        np.save(self.vectors_path, combined)

    def _append_records(self, records: List[Dict[str, Any]]) -> None:
        with open(self.records_path, "a", encoding="utf-8") as f:
            for r in records:
                json.dump(
                    {
                        "text": r["text"],
                        "metadata": r["metadata"],
                    },
                    f,
                )
                f.write("\n")

    # ======================================================
    # Validation & safety
    # ======================================================

    def _validate_vectors(self, vectors: np.ndarray) -> None:
        if vectors.ndim != 2:
            raise ValueError("Vectors must be 2D array")

        if vectors.shape[1] != self.vector_dim:
            raise ValueError(
                f"Vector dim mismatch: expected {self.vector_dim}"
            )

        if vectors.dtype != np.float32:
            raise ValueError("Vectors must be float32")
