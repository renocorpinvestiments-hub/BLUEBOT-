# retrieval_planner.py
from __future__ import annotations

from typing import List, Dict, Any
import re


class RetrievalPlanner:
    """
    Institution-grade internet retrieval planner.

    ROLE:
    - Convert user intent into search-ready queries
    - Expand semantic surface area
    - Remain search-engine agnostic
    """

    MAX_QUERIES = 5
    MAX_QUERY_LENGTH = 128

    def __init__(
        self,
        enable_expansion: bool = True,
        language: str = "en",
    ):
        self.enable_expansion = enable_expansion
        self.language = language

    # ======================================================
    # Public API
    # ======================================================

    def build_plan(self, request_text: str) -> Dict[str, Any]:
        """
        Main entrypoint.
        Returns a structured retrieval plan.
        """

        cleaned = self._clean_text(request_text)

        base_query = self._base_query(cleaned)

        queries = [base_query]

        if self.enable_expansion:
            expanded = self._expand_query(base_query)
            queries.extend(expanded)

        queries = self._deduplicate(queries)
        queries = self._truncate_queries(queries)

        return {
            "type": "INTERNET_RETRIEVAL",
            "language": self.language,
            "queries": queries,
            "original_request": request_text,
        }

    # ======================================================
    # Query construction internals
    # ======================================================

    def _clean_text(self, text: str) -> str:
        """
        Normalizes whitespace and removes noise.
        """
        text = text.strip().lower()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[^\w\s\-\?]", "", text)
        return text

    def _base_query(self, text: str) -> str:
        """
        Generates the core search query.
        """
        return text[: self.MAX_QUERY_LENGTH]

    def _expand_query(self, query: str) -> List[str]:
        """
        Expands query using deterministic heuristics.
        NO ML.
        """

        expansions = []

        # Question reformulation
        if query.startswith(("what", "why", "how")):
            expansions.append(query.replace("what is", "").strip())
            expansions.append(f"definition of {query}")

        # Intent enrichment
        if "meaning" in query:
            expansions.append(query.replace("meaning", "definition"))
            expansions.append(f"explanation of {query}")

        if "symptoms" in query:
            expansions.append(f"signs of {query}")

        # Contextual broadening
        expansions.append(f"{query} overview")
        expansions.append(f"{query} explained")

        return expansions

    # ======================================================
    # Safety & normalization
    # ======================================================

    def _deduplicate(self, queries: List[str]) -> List[str]:
        seen = set()
        unique = []
        for q in queries:
            if q not in seen:
                seen.add(q)
                unique.append(q)
        return unique

    def _truncate_queries(self, queries: List[str]) -> List[str]:
        """
        Enforces limits for search APIs.
        """
        return queries[: self.MAX_QUERIES]
