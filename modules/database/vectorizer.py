# vectorizer.py
from __future__ import annotations

import threading
from typing import List, Dict, Any, Tuple
import numpy as np
import torch
from transformers import AutoTokenizer, AutoModel


class Vectorizer:
    """
    Institution-grade vectorization engine.
    Converts raw text into normalized embedding vectors.
    """

    _lock = threading.Lock()

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        device: str | None = None,
    ):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name)
        self.model.to(self.device)
        self.model.eval()

    @torch.no_grad()
    def _embed_batch(self, texts: List[str]) -> np.ndarray:
        """
        Internal batched embedding.
        """
        inputs = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            return_tensors="pt",
        ).to(self.device)

        outputs = self.model(**inputs)
        embeddings = outputs.last_hidden_state[:, 0, :]

        vectors = embeddings.cpu().numpy()
        return self._l2_normalize(vectors)

    @staticmethod
    def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / (norms + 1e-12)

    def vectorize(
        self,
        texts: List[str],
        metadata: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Public API.
        Returns vector records ready for storage.
        """
        if len(texts) != len(metadata):
            raise ValueError("Texts and metadata length mismatch")

        with self._lock:
            vectors = self._embed_batch(texts)

        records = []
        for text, vector, meta in zip(texts, vectors, metadata):
            records.append(
                {
                    "vector": vector.astype(np.float32),
                    "text": text,
                    "metadata": meta,
                }
            )

        return records
