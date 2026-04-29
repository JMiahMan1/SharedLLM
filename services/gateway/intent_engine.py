# services/gateway/intent_engine.py
"""
Semantic Router for the Gateway Service.
Classifies intents rapidly using sentence-transformers to bypass LLMs for known commands.
"""
import os
import json
import logging
import numpy as np
from typing import Tuple

log = logging.getLogger("gateway.intent_engine")

class IntentEngine:
    def __init__(self):
        self.model = None
        self.intent_embeddings = []
        self.intent_labels = []
        self.phrasebook_path = os.getenv("PHRASEBOOK_PATH", "/app/data/phrasebook.json")

    def load(self):
        from sentence_transformers import SentenceTransformer
        model_name = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        log.info(f"Loading intent engine model: {model_name}")
        self.model = SentenceTransformer(model_name)
        
        if not os.path.exists(self.phrasebook_path):
            log.warning(f"Phrasebook not found at {self.phrasebook_path}, using defaults.")
            self._load_defaults()
        else:
            try:
                with open(self.phrasebook_path, "r") as f:
                    data = json.load(f)
                    self._vectorize(data)
            except Exception as e:
                log.error(f"Failed to load phrasebook: {e}")
                self._load_defaults()

    def _load_defaults(self):
        defaults = {
            "turn_on": ["turn on the lights", "power on", "switch on"],
            "turn_off": ["turn off the lights", "power off", "switch off"],
            "play_media": ["play music", "start playing", "resume"],
            "pause_media": ["pause music", "stop playing", "pause"],
        }
        self._vectorize(defaults)

    def _vectorize(self, data: dict):
        phrases = []
        labels = []
        for intent, examples in data.items():
            for ex in examples:
                phrases.append(ex.lower())
                labels.append(intent)
                
        if not phrases:
            return
            
        embeddings = self.model.encode(phrases)
        self.intent_embeddings = embeddings
        self.intent_labels = labels
        log.info(f"Intent engine ready. Vectorized {len(phrases)} phrases across {len(data)} intents.")

    def classify(self, query: str) -> Tuple[str, float]:
        """Returns (intent_name, confidence_score)."""
        if not self.model or len(self.intent_embeddings) == 0:
            return "unknown", 0.0
            
        query_emb = self.model.encode([query.lower()])[0]
        
        # Calculate cosine similarity manually using numpy for speed
        query_norm = np.linalg.norm(query_emb)
        if query_norm == 0:
            return "unknown", 0.0
            
        similarities = []
        for emb in self.intent_embeddings:
            emb_norm = np.linalg.norm(emb)
            if emb_norm == 0:
                similarities.append(0.0)
            else:
                sim = np.dot(query_emb, emb) / (query_norm * emb_norm)
                similarities.append(sim)
                
        max_idx = np.argmax(similarities)
        best_score = float(similarities[max_idx])
        best_intent = self.intent_labels[max_idx]
        
        return best_intent, best_score

engine = IntentEngine()
