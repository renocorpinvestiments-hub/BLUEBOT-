# searcher.py
from __future__ import annotations

from typing import List, Dict, Any, Tuple
import numpy as np


class VectorSearcher:
    """
    High-performance cosine similarity search engine.
    """

    def __init__(self, vectors: np.ndarray, records: List[Dict[str, Any]]):
        """
        vectors: shape (N, D), MUST be L2-normalized
        records: aligned metadata/text records
        """
        if vectors.ndim != 2:
            raise ValueError("Vectors must be 2D array")

        self.vectors = vectors.astype(np.float32)
        self.records = records

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        Cosine similarity search.
        """
        if query_vector.ndim != 1:
            raise ValueError("Query vector must be 1D")

        query_vector = query_vector.astype(np.float32)

        scores = np.dot(self.vectors, query_vector)

        top_indices = np.argpartition(scores, -top_k)[-top_k:]
        top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        results = []
        for idx in top_indices:
            score = float(scores[idx])
            if score < min_score:
                continue

            record = self.records[idx]
            results.append(
                {
                    "score": score,
                    "text": record["text"],
                    "metadata": record["metadata"],
                }
            )

        return results
