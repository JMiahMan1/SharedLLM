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

import re
import difflib
from typing import Tuple, Optional, List, Dict

log = logging.getLogger("gateway.intent_engine")

class IntentEngine:
    def __init__(self):
        self.model = None
        self.intent_embeddings = np.array([]) if np is not None else []
        self.intent_labels = []
        
        # Entity Cache: friendly_name -> entity_id (e.g. "piano lamp" -> "light.piano_lamp")
        self.entity_cache: Dict[str, str] = {}
        
        # Update path to local relative path for development, or use ENV
        self.phrasebook_path = os.getenv(
            "PHRASEBOOK_PATH", 
            os.path.join(os.path.dirname(__file__), "data", "phrasebook.json")
        )
        
        # Pull threshold from ENV so the React Admin UI can dynamically tune it
        self.FAST_PATH_CONFIDENCE = float(os.getenv("FAST_PATH_THRESHOLD", "0.85"))
        self.is_active = False

    def load(self):
        # ... (keep existing load logic)
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
                
            self.is_active = True
            log.info("Semantic Router loaded successfully. Ready for fast-path inference.")
            
        except Exception as e:
            self.model = None
            self.is_active = False
            log.error(f"Failed to load Semantic Router: {e}. Gracefully degrading to LLM intent engine.")

    def update_entity_cache(self, entities: List[Dict[str, Any]]):
        """Updates the local cache of friendly names for fuzzy matching."""
        new_cache = {}
        for ent in entities:
            eid = ent.get("entity_id")
            attr = ent.get("attributes", {})
            fname = attr.get("friendly_name", "").lower()
            if fname:
                new_cache[fname] = eid
            # Also index the ID itself (stripped of domain)
            short_id = eid.split(".")[-1].replace("_", " ")
            if short_id and short_id not in new_cache:
                new_cache[short_id] = eid
        
        self.entity_cache = new_cache
        log.info(f"IntentEngine: Updated entity cache with {len(new_cache)} entries.")

    def _vectorize(self, data: dict):
        # ... (keep existing _vectorize logic)
        phrases = []
        labels = []
        for intent, examples in data.items():
            if intent == "fallbacks":
                continue 
            for ex in examples:
                phrases.append(ex.lower())
                labels.append(intent)

        if not phrases:
            log.warning("Phrasebook is empty.")
            return
            
        embeddings = list(self.model.embed(phrases))
        if np is not None:
            self.intent_embeddings = np.array(embeddings)
        else:
            self.intent_embeddings = embeddings
        self.intent_labels = labels
        log.info(f"Semantic Router initialized. Indexed {len(phrases)} examples.")

    def extract_entity(self, query: str, intent: str) -> Optional[str]:
        """
        Regex-based extraction of the target entity from a natural language query.
        Returns the resolved entity_id if a match is found via fuzzy lookup.
        """
        q = query.lower().strip()
        target = None

        # Regex patterns for common control intents
        patterns = {
            "turn_on": [r"turn on (?:the )?(.+)", r"power on (?:the )?(.+)", r"switch on (?:the )?(.+)"],
            "turn_off": [r"turn off (?:the )?(.+)", r"power off (?:the )?(.+)", r"switch off (?:the )?(.+)"],
            "play_media": [r"play (?:the )?(.+)", r"start (?:the )?(.+)", r"put on (?:the )?(.+)"],
        }

        if intent in patterns:
            for p in patterns[intent]:
                match = re.search(p, q)
                if match:
                    target = match.group(1).strip()
                    # Clean up common trailers
                    target = re.sub(r"\b(?:please|now|right away)\b", "", target).strip()
                    break
        
        if not target:
            return None

        # Fuzzy Matching against cache
        friendly_names = list(self.entity_cache.keys())
        matches = difflib.get_close_matches(target, friendly_names, n=1, cutoff=0.7)
        
        if matches:
            resolved_id = self.entity_cache[matches[0]]
            log.info(f"[FastPath] Resolved '{target}' to '{resolved_id}' (via fuzzy match '{matches[0]}')")
            return resolved_id
        
        log.warning(f"[FastPath] Could not resolve entity from target string: '{target}'")
        return None

    def classify(self, query: str) -> Tuple[str, float]:
        """
        Classifies the query into an intent.
        Returns (intent_name, confidence_score).
        """
        q = query.lower().strip()
        
        # 1. Hardcoded Keyword Fallbacks (Safety/Test logic)
        # We only use these for VERY simple queries to avoid hijacking complex ones.
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
        if not self.is_active or not self.model or len(self.intent_embeddings) == 0 or np is None:
            return "unknown", 0.0
            
        try:
            query_emb = list(self.model.embed([query.lower()]))[0]
            query_norm = np.linalg.norm(query_emb)
            if query_norm == 0:
                return "unknown", 0.0
                
            norms = np.linalg.norm(self.intent_embeddings, axis=1)
            dots = np.dot(self.intent_embeddings, query_emb)
            similarities = dots / (query_norm * norms + 1e-9)
            
            max_idx = np.argmax(similarities)
            best_score = float(similarities[max_idx])
            best_intent = self.intent_labels[max_idx]

            return best_intent, best_score
            
        except Exception as e:
            log.error(f"Semantic Router classification error: {e}")
            return "unknown", 0.0

    def should_bypass_llm(self, confidence: float) -> bool:
        return confidence >= self.FAST_PATH_CONFIDENCE

    def is_fast_path(self, intent: str, confidence: float) -> bool:
        if intent == "unknown":
            return False
        return self.should_bypass_llm(confidence)

engine = IntentEngine()

