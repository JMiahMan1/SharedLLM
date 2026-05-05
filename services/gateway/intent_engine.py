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
        self.phrasebook_path = os.getenv("PHRASEBOOK_PATH", "/app/data/phrasebook.json")

    def load(self):
        try:
            from fastembed import TextEmbedding
            model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
            log.info(f"Loading intent engine model: {model_name}")
            self.model = TextEmbedding(model_name=model_name)
            
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
        except Exception as e:
            log.error(f"Critical error loading IntentEngine: {e}")

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
            
        # FastEmbed returns a generator of embeddings
        embeddings = list(self.model.embed(phrases))
        self.intent_embeddings = np.array(embeddings)
        self.intent_labels = labels
        log.info(f"Intent engine ready. Vectorized {len(phrases)} phrases across {len(data)} intents.")

    def _regex_override(self, query: str) -> str | None:
        q = (query or "").strip().lower()
        if not q:
            return None

        regex_intent_map = (
            (r"^(?:turn on|switch on|power on)\b", "turn_on"),
            (r"^(?:turn off|switch off|power off)\b", "turn_off"),
            (r"^(?:play|listen to|listen|watch)\b", "play_media"),
            (r"^(?:resume)\b", "play_media"),
            (r"^(?:pause|stop)\b", "pause_media"),
            (r"^(?:toggle|flip)\b", "toggle"),
            (r"^(?:fix|update|implement|change|patch|create)\b.*(?:code|file|module|service|script|program)", "code_orchestrate"),
        )
        for pattern, intent in regex_intent_map:
            if re.match(pattern, q, flags=re.IGNORECASE):
                return intent
        return None

    def _keyword_fallback(self, query: str) -> Tuple[str, float]:
        q = (query or "").lower()
        if any(token in q for token in ("turn on", "switch on", "power on")):
            return "turn_on", 0.75
        if any(token in q for token in ("turn off", "switch off", "power off")):
            return "turn_off", 0.75
        if any(token in q for token in ("play", "listen", "watch", "resume")):
            return "play_media", 0.7
        if any(token in q for token in ("pause", "stop")):
            return "pause_media", 0.7
        if any(token in q for token in ("toggle", "flip")):
            return "toggle", 0.9
        return "unknown", 0.0

    def classify(self, query: str) -> Tuple[str, float]:
        """Returns (intent_name, confidence_score)."""
        regex_intent = self._regex_override(query)
        if regex_intent:
            return regex_intent, 1.0

        if not self.model or len(self.intent_embeddings) == 0:
            return self._keyword_fallback(query)
            
        # FastEmbed returns a generator
        query_emb = list(self.model.embed([query.lower()]))[0]
        
        # Calculate cosine similarity using numpy
        query_norm = np.linalg.norm(query_emb)
        if query_norm == 0:
            return "unknown", 0.0
            
        # Broadcast similarity calculation
        norms = np.linalg.norm(self.intent_embeddings, axis=1)
        dots = np.dot(self.intent_embeddings, query_emb)
        similarities = dots / (query_norm * norms + 1e-9)
        
        max_idx = np.argmax(similarities)
        best_score = float(similarities[max_idx])
        best_intent = self.intent_labels[max_idx]

        if best_score < 0.5:
            return self._keyword_fallback(query)
        return best_intent, best_score

engine = IntentEngine()
