# ingestion_pipeline.py
from __future__ import annotations

from typing import List, Dict, Any, Iterable
import re

from vectorizer import Vectorizer
from vector_store import VectorStore


class IngestionPipeline:
    """
    Institution-grade ingestion pipeline.

    ROLE:
    - Normalize external content
    - Chunk intelligently
    - Vectorize efficiently
    - Persist safely into VectorStore
    """

    def __init__(
        self,
        vectorizer: Vectorizer,
        vector_store: VectorStore,
        max_chunk_size: int = 512,
        min_chunk_size: int = 32,
    ):
        self.vectorizer = vectorizer
        self.vector_store = vector_store
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size

    # ======================================================
    # Public API
    # ======================================================

    def ingest(
        self,
        documents: Iterable[Dict[str, Any]],
        source: str,
    ) -> int:
        """
        Main ingestion entrypoint.

        documents: iterable of raw content dicts
        source: identifier (internet, book, archive, api)

        Returns: number of chunks ingested
        """

        texts: List[str] = []
        metadata: List[Dict[str, Any]] = []

        for doc in documents:
            raw_text = doc.get("text", "")
            if not raw_text or not raw_text.strip():
                continue

            chunks = self._chunk_text(raw_text)

            for idx, chunk in enumerate(chunks):
                texts.append(chunk)
                metadata.append(
                    self._build_metadata(doc, source, idx)
                )

        if not texts:
            return 0

        records = self.vectorizer.vectorize(texts, metadata)
        self.vector_store.add(records)

        return len(records)

    # ======================================================
    # Chunking logic
    # ======================================================

    def _chunk_text(self, text: str) -> List[str]:
        """
        Splits text into semantically reasonable chunks.
        """

        text = self._clean_text(text)
        if len(text) <= self.max_chunk_size:
            return [text]

        sentences = self._split_sentences(text)

        chunks = []
        current = ""

        for sentence in sentences:
            if len(current) + len(sentence) <= self.max_chunk_size:
                current += " " + sentence if current else sentence
            else:
                if len(current) >= self.min_chunk_size:
                    chunks.append(current.strip())
                current = sentence

        if len(current) >= self.min_chunk_size:
            chunks.append(current.strip())

        return chunks

    # ======================================================
    # Text normalization
    # ======================================================

    def _clean_text(self, text: str) -> str:
        text = text.strip()
        text = re.sub(r"\s+", " ", text)
        return text

    def _split_sentences(self, text: str) -> List[str]:
        """
        Lightweight sentence splitting.
        No NLP dependency.
        """
        return re.split(r"(?<=[.!?])\s+", text)

    # ======================================================
    # Metadata construction
    # ======================================================

    def _build_metadata(
        self,
        doc: Dict[str, Any],
        source: str,
        chunk_index: int,
    ) -> Dict[str, Any]:
        """
        Builds stable metadata for each chunk.
        """
        return {
            "source": source,
            "title": doc.get("title"),
            "url": doc.get("url"),
            "author": doc.get("author"),
            "chunk_index": chunk_index,
            "document_id": doc.get("id"),
        }
