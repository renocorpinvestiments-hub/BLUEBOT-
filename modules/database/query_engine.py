# query_engine.py
from __future__ import annotations

from typing import List, Dict, Any, Optional

import numpy as np

from vectorizer import Vectorizer
from searcher import VectorSearcher
from devectorizer import Devectorizer


class QueryEngine:
    """
    Institution-grade query orchestration engine.

    ROLE:
    - Accept user request
    - Vectorize query
    - Search vector store
    - Decide: HIT or MISS
    - Route to response or retrieval planner
    """

    def __init__(
        self,
        vectorizer: Vectorizer,
        devectorizer: Devectorizer,
        similarity_threshold: float = 0.75,
        top_k: int = 5,
    ):
        """
        Dependencies are injected to keep this engine testable and fast.
        """
        self.vectorizer = vectorizer
        self.devectorizer = devectorizer
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k

    def process_request(
        self,
        request_text: str,
        vector_store: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Main entrypoint.

        vector_store MUST expose:
        - vectors: np.ndarray
        - records: List[Dict]
        """

        # --------------------------------------------------
        # 1. Guard clauses
        # --------------------------------------------------
        if not request_text or not request_text.strip():
            raise ValueError("Empty request_text")

        if not vector_store.get("vectors") is not None:
            raise ValueError("Vector store missing vectors")

        if len(vector_store["vectors"]) == 0:
            return self._no_data_response(request_text)

        # --------------------------------------------------
        # 2. Vectorize user request
        # --------------------------------------------------
        query_record = self.vectorizer.vectorize(
            texts=[request_text],
            metadata=[{"type": "query"}],
        )[0]

        query_vector = query_record["vector"]

        # --------------------------------------------------
        # 3. Search existing vectors
        # --------------------------------------------------
        searcher = VectorSearcher(
            vectors=vector_store["vectors"],
            records=vector_store["records"],
        )

        search_results = searcher.search(
            query_vector=query_vector,
            top_k=self.top_k,
            min_score=0.0,
        )

        if not search_results:
            return self._no_match_response(request_text)

        # --------------------------------------------------
        # 4. Evaluate confidence
        # --------------------------------------------------
        best_score = search_results[0]["score"]

        if best_score < self.similarity_threshold:
            return self._low_confidence_response(
                request_text=request_text,
                best_score=best_score,
            )

        # --------------------------------------------------
        # 5. Successful semantic hit
        # --------------------------------------------------
        formatted = self.devectorizer.format_results(search_results)

        return {
            "status": "HIT",
            "confidence": round(best_score, 4),
            "results": formatted,
        }

    # ======================================================
    # Internal response builders (private)
    # ======================================================

    def _no_data_response(self, request_text: str) -> Dict[str, Any]:
        """
        Vector DB is empty.
        """
        return {
            "status": "NO_DATA",
            "action": "RETRIEVE",
            "reason": "Vector store empty",
            "query": request_text,
        }

    def _no_match_response(self, request_text: str) -> Dict[str, Any]:
        """
        Vector DB exists but nothing matched.
        """
        return {
            "status": "MISS",
            "action": "RETRIEVE",
            "reason": "No semantic matches found",
            "query": request_text,
        }

    def _low_confidence_response(
        self,
        request_text: str,
        best_score: float,
    ) -> Dict[str, Any]:
        """
        Weak semantic match.
        """
        return {
            "status": "LOW_CONFIDENCE",
            "action": "RETRIEVE",
            "confidence": round(best_score, 4),
            "query": request_text,
        }
