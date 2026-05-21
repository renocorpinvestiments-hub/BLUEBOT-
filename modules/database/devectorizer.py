# devectorizer.py
from __future__ import annotations

from typing import List, Dict, Any


class Devectorizer:
    """
    Converts vector search results into human-readable knowledge.
    """

    def __init__(self):
        pass

    def format_results(
        self,
        search_results: List[Dict[str, Any]],
        include_score: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Normalizes output for presentation or reasoning layers.
        """
        formatted = []

        for result in search_results:
            entry = {
                "text": result["text"],
                "metadata": result["metadata"],
            }

            if include_score:
                entry["similarity"] = round(result["score"], 4)

            formatted.append(entry)

        return formatted

    def to_prompt_context(self, search_results: List[Dict[str, Any]]) -> str:
        """
        Builds a clean semantic context block (LLM-ready).
        """
        lines = []
        for r in search_results:
            ref = r["metadata"].get("reference", "")
            lines.append(f"- {r['text']} ({ref})")

        return "\n".join(lines)
