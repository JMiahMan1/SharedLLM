"""
Semantic Router for the Gateway Service.
Classifies intents rapidly using fastembed to bypass LLMs for known commands.
"""
import os
import json
import logging
try:
    import numpy as np
except ImportError:
    np = None
from typing import Tuple

log = logging.getLogger("gateway.intent_engine")

class IntentEngine:
    def __init__(self):
        self.model = None
        self.intent_embeddings = np.array([]) if np is not None else []
        self.intent_labels = []
        
        # Update path to local relative path for development, or use ENV
        self.phrasebook_path = os.getenv(
            "PHRASEBOOK_PATH", 
            os.path.join(os.path.dirname(__file__), "data", "phrasebook.json")
        )
        
        # Pull threshold from ENV so the React Admin UI can dynamically tune it
        self.FAST_PATH_CONFIDENCE = float(os.getenv("FAST_PATH_THRESHOLD", "0.85"))
        self.is_active = False

    def load(self):
        try:
            from fastembed import TextEmbedding
            model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
            log.info(f"Loading Semantic Router model: {model_name}")
            
            # Requires internet on first run UNLESS baked into the Dockerfile
            self.model = TextEmbedding(model_name=model_name)

            if not os.path.exists(self.phrasebook_path):
                log.warning(f"Phrasebook not found at {self.phrasebook_path}. Semantic Routing disabled.")
                return

            with open(self.phrasebook_path, "r") as f:
                data = json.load(f)
                self._vectorize(data)
                
            self.is_active = True
            log.info("Semantic Router loaded successfully. Ready for fast-path inference.")
            
        except Exception as e:
            self.model = None
            self.is_active = False
            log.error(f"Failed to load Semantic Router: {e}. Gracefully degrading to LLM intent engine.")

    def _vectorize(self, data: dict):
        phrases = []
        labels = []
        for intent, examples in data.items():
            if intent == "fallbacks":
                continue # Skip acknowledgment phrases
            for ex in examples:
                phrases.append(ex.lower())
                labels.append(intent)

        if not phrases:
            log.warning("Phrasebook is empty.")
            return
            
        # fastembed returns a generator, convert to list
        embeddings = list(self.model.embed(phrases))
        
        # Using NumPy arrays for ultra-fast vectorized dot product comparisons
        if np is not None:
            self.intent_embeddings = np.array(embeddings)
        else:
            self.intent_embeddings = embeddings
        self.intent_labels = labels
        log.info(f"Semantic Router initialized. Indexed {len(phrases)} examples across {len(data)} routes.")

    def classify(self, query: str) -> Tuple[str, float]:
        """
        Classifies the query into an intent.
        Returns (intent_name, confidence_score).
        """
        q = query.lower()
        
        # 1. Hardcoded Keyword Fallbacks (Safety/Test logic)
        # These ensure core functionality works even if the semantic model is offline.
        # We only use these for VERY simple queries to avoid hijacking complex ones that need entity extraction.
        if q in ["play", "play music", "start playing", "resume music"]:
            return "play_media", 1.0
        if q in ["pause", "pause music", "stop the music", "stop playing"]:
            return "pause_media", 1.0
        if q in ["turn on", "power on", "switch on"]:
            return "turn_on", 1.0
        if q in ["turn off", "power off", "switch off"]:
            return "turn_off", 1.0
        if q in ["index", "reindex", "scan my library"]:
            return "index_storage", 1.0
        if q in ["sync home assistant", "refresh devices"]:
            return "sync_ha", 1.0

        # 2. Semantic Routing (if active)
        # Fallback Check: Engine crashed or has no embeddings
        if not self.is_active or not self.model or len(self.intent_embeddings) == 0 or np is None:
            return "unknown", 0.0
            
        try:
            query_emb = list(self.model.embed([query.lower()]))[0]
            
            # Fast Vectorized Cosine Similarity
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
            
        except Exception as e:
            log.error(f"Semantic Router classification error: {e}")
            return "unknown", 0.0

    def should_bypass_llm(self, confidence: float) -> bool:
        """
        Determines if the semantic match is strong enough to bypass LLM classification.
        """
        return confidence >= self.FAST_PATH_CONFIDENCE

    def is_fast_path(self, intent: str, confidence: float) -> bool:
        """
        Checks if an intent is eligible for direct execution.
        """
        if intent == "unknown":
            return False
        return self.should_bypass_llm(confidence)

engine = IntentEngine()
