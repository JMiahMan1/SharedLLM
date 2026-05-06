# services/gateway/intent_engine.py
"""
Semantic Router for the Gateway Service.
Classifies intents rapidly using fastembed to bypass LLMs for known commands.
"""
import os
import json
import logging
import numpy as np
import re
from typing import Tuple

log = logging.getLogger("gateway.intent_engine")

class IntentEngine:
    def __init__(self):
        self.model = None
        self.intent_embeddings = []
        self.intent_labels = []
        # Update path to local relative path for development, or use ENV
        self.phrasebook_path = os.getenv("PHRASEBOOK_PATH", os.path.join(os.path.dirname(__file__), "data", "phrasebook.json"))
        self.FAST_PATH_CONFIDENCE = 0.85

    def load(self):
        try:
            from fastembed import TextEmbedding
            model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
            log.info(f"Loading Semantic Router model: {model_name}")
            self.model = TextEmbedding(model_name=model_name)
            
            if not os.path.exists(self.phrasebook_path):
                log.warning(f"Phrasebook not found at {self.phrasebook_path}. Semantic Routing disabled.")
                return
            
            with open(self.phrasebook_path, "r") as f:
                data = json.load(f)
                self._vectorize(data)
        except Exception as e:
            log.error(f"Critical error loading IntentEngine: {e}")

    def _vectorize(self, data: dict):
        phrases = []
        labels = []
        for intent, examples in data.items():
            for ex in examples:
                phrases.append(ex.lower())
                labels.append(intent)
                
        if not phrases:
            return
            
        embeddings = list(self.model.embed(phrases))
        self.intent_embeddings = np.array(embeddings)
        self.intent_labels = labels
        log.info(f"Semantic Router initialized. Indexed {len(phrases)} examples across {len(data)} routes.")

    def classify(self, query: str) -> Tuple[str, float]:
        """
        Classifies the query into an intent.
        Returns (intent_name, confidence_score).
        """
        if not self.model or len(self.intent_embeddings) == 0:
            return "unknown", 0.0
            
        query_emb = list(self.model.embed([query.lower()]))[0]
        
        # Calculate cosine similarity
        query_norm = np.linalg.norm(query_emb)
        if query_norm == 0:
            return "unknown", 0.0
            
        norms = np.linalg.norm(self.intent_embeddings, axis=1)
        dots = np.dot(self.intent_embeddings, query_emb)
        similarities = dots / (query_norm * norms + 1e-9)
        
        max_idx = np.argmax(similarities)
        best_score = float(similarities[max_idx])
        best_intent = self.intent_labels[max_idx]

        log.info(f"[SemanticRouter] Match: '{best_intent}' with confidence {best_score:.4f}")
        return best_intent, best_score

    def should_bypass_llm(self, confidence: float) -> bool:
        """Determines if the confidence is high enough to skip LLM generation."""
        return confidence >= self.FAST_PATH_CONFIDENCE

engine = IntentEngine()
