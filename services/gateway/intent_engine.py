"""
Semantic Router for the Gateway Service.
Classifies intents rapidly using fastembed to bypass LLMs for known commands.
"""
import json
import logging
import os

from services.gateway.config import EMBEDDING_MODEL, FAST_PATH_THRESHOLD, PHRASEBOOK_PATH

try:
    import numpy as np
except ImportError:
    np = None
import difflib
import re
from typing import Any

log = logging.getLogger("gateway.intent_engine")

# Explicit Raven invocation keywords. A Raven autonomous mission is only
# triggered when the user EXPLICITLY invokes Raven (e.g. "Raven, build me ...")
# AND pairs it with an actionable command. This prevents generic requests
# (e.g. "system diagnostic and maintenance scan") from being force-routed to
# the slow/expensive Raven mission queue just because they contain words like
# "scan", "check", or "build".
RAVEN_KEYWORDS = ("raven", "use raven")
RAVEN_COMMAND_VERBS = (
    "build", "create", "make", "develop", "implement", "fix", "repair",
    "audit", "scan", "index", "reindex", "deploy", "refactor", "scaffold",
    "generate", "write", "check", "update", "convert", "review", "bootstrap",
    "sync", "synchronize", "diagnose", "maintain", "maintenance", "setup",
    "set up", "prototype", "run",
)


def is_raven_intent(query: str | None) -> bool:
    """True only when the prompt explicitly invokes Raven AND issues a command.

    Used by the chat path, the orchestrator, and the worker to decide whether a
    request should become a Raven autonomous mission. Requiring both a Raven
    keyword and a command verb avoids false positives on ordinary queries.
    """
    if not query:
        return False
    q = query.lower()
    if not any(k in q for k in RAVEN_KEYWORDS):
        return False
    return any(v in q for v in RAVEN_COMMAND_VERBS)


class IntentEngine:
    def __init__(self):
        self.model = None
        self.intent_embeddings = np.array([]) if np is not None else []
        self.intent_labels = []

        # Entity Cache: friendly_name -> entity_id (e.g. "piano lamp" -> "light.piano_lamp")
        self.entity_cache: dict[str, str] = {}

        # Update path to local relative path for development, or use ENV
        self.phrasebook_path = PHRASEBOOK_PATH or os.path.join(os.path.dirname(__file__), "data", "phrasebook.json")

        # Pull threshold from ENV so the React Admin UI can dynamically tune it
        self.FAST_PATH_CONFIDENCE = FAST_PATH_THRESHOLD
        self.is_active = False

    def load(self):
        # ... (keep existing load logic)
        try:
            from fastembed import TextEmbedding  # pyright: ignore[reportMissingImports]
            model_name = EMBEDDING_MODEL
            log.info(f"Loading Semantic Router model: {model_name}")

            self.model = TextEmbedding(model_name=model_name)

            if not os.path.exists(self.phrasebook_path):
                log.warning(f"Phrasebook not found at {self.phrasebook_path}. Semantic Routing disabled.")
                return

            with open(self.phrasebook_path) as f:
                data = json.load(f)
                self._vectorize(data)

            self.is_active = True
            log.info("Semantic Router loaded successfully. Ready for fast-path inference.")

        except Exception as e:
            self.model = None
            self.is_active = False
            log.error(f"Failed to load Semantic Router: {e}. Gracefully degrading to LLM intent engine.")

    def update_entity_cache(self, entities: list[dict[str, Any]]):
        """Updates the local cache of friendly names for fuzzy matching."""
        new_cache = {}
        for ent in entities:
            eid = ent.get("entity_id")
            attr = ent.get("attributes", {})
            fname = attr.get("friendly_name", "").lower()
            if fname:
                new_cache[fname] = eid
            # Also index the ID itself (stripped of domain)
            short_id = eid.split(".")[-1].replace("_", " ") if eid else ""
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

        if self.model is None:
            log.error("Cannot embed: model not loaded.")
            return
        embeddings = list(self.model.embed(phrases))
        if np is not None:
            self.intent_embeddings = np.array(embeddings)
        else:
            self.intent_embeddings = embeddings
        self.intent_labels = labels
        log.info(f"Semantic Router initialized. Indexed {len(phrases)} examples.")

    def extract_entity(self, query: str, intent: str) -> str | None:
        """
        Regex-based extraction of the target entity from a natural language query.
        Returns the resolved entity_id if a match is found via fuzzy lookup.
        """
        q = query.lower().strip()
        target = None

        # Regex patterns for common control intents
        patterns = {
            "turn_on": [
                r"^(?:turn|switch|power)\s+on\s+(?:the\s+)?(.+)$",
                r"^(?:turn|switch|power)\s+(?:the\s+)?(.+?)\s+on$",
                r"^(?:the\s+)?(.+?)\s+on$",
            ],
            "turn_off": [
                r"^(?:turn|switch|power|shut)\s+off\s+(?:the\s+)?(.+)$",
                r"^(?:turn|switch|power|shut)\s+(?:the\s+)?(.+?)\s+off$",
                r"^(?:the\s+)?(.+?)\s+off$",
            ],
            "play_media": [
                r"play .*(?:on|in|at) (.+)",
                r"listen to .*(?:on|in|at) (.+)",
                r"put on .*(?:on|in|at) (.+)",
            ],
            "pause_media": [
                r"pause (?:the )?(?:music|video|media )?(?:on|in|at) (.+)",
                r"stop (?:the )?(?:music|video|media )?(?:on|in|at) (.+)",
            ],
            "media_transport": [
                r"(?:pause|stop|resume|skip|next|rewind|fast forward).*(?:on|in|at) (.+)",
            ],
        }

        q_clean = query.lower().strip().strip("?.!")
        if "," in q_clean:
            q_clean = q_clean.split(",")[0].strip()

        if intent in patterns:
            for p in patterns[intent]:
                match = re.search(p, q_clean)
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

        # Normalized Substring / Token Matching fallback
        target_norm = re.sub(r'[^a-z0-9]+', '', target)
        if target_norm:
            for fname, eid in self.entity_cache.items():
                fname_norm = re.sub(r'[^a-z0-9]+', '', fname)
                if target_norm == fname_norm or target_norm in fname_norm or fname_norm in target_norm:
                    log.info(f"[FastPath] Resolved '{target}' to '{eid}' (via normalized match '{fname}')")
                    return eid

        log.warning(f"[FastPath] Could not resolve entity from target string: '{target}'")
        return None

    def classify(self, query: str) -> tuple[str, float]:
        """
        Classifies the query into an intent.
        Returns (intent_name, confidence_score).
        """
        # 0. Autonomous / Raven check: if query explicitly invokes Raven, never fast-path
        if is_raven_intent(query):
            return "raven_mission", 0.0

        q = query.lower().strip().strip("?.!")
        if "," in q:
            parts = [p.strip() for p in q.split(",") if p.strip()]
            if len(parts) > 1 and (parts[0] in parts[1] or parts[1] in parts[0]):
                q = parts[0]
            elif len(parts) > 1 and any(w in parts[0] for w in ["turn", "play", "pause", "resume", "stop", "switch", "lights", "light", "skip", "next"]):
                q = parts[0]

        # 1. Fast Pattern & Keyword Detection (immediate 1.0 confidence for media/home control)
        # Media Transport: pause, stop, resume, next, skip, previous, volume
        transport_words = {"pause", "stop", "resume", "unpause", "next", "skip", "prev", "previous", "next track", "next song", "skip song", "previous track", "volume up", "volume down", "louder", "quieter"}
        if q in transport_words:
            return "media_transport", 1.0

        if re.search(r"^(?:pause|stop|resume|unpause|skip|next|prev|previous)(?:\s+(?:the\s+)?(?:music|playback|audio|podcast|track|song|video|media))?(?:\s+(?:on|in|at)\s+.+)?$", q):
            return "media_transport", 1.0

        if re.search(r"^(?:next|skip|prev|previous)\s+(?:(?:this|the)\s+)?(?:track|song|music|audio|episode|item)\b", q):
            return "media_transport", 1.0

        if re.search(r"^(?:volume\s+(?:up|down)|turn\s+(?:it\s+)?(?:up|down)|louder|quieter|mute|unmute)\b", q):
            return "media_transport", 1.0

        # Media Play: "play <content>", "listen to <content>", "put on <content>", "stream <content>"
        if re.search(r"^(?:play|listen to|put on|stream|start playing)\b", q):
            if not re.search(r"^(?:play\s+(?:a\s+game|roles|dumb))\b", q):
                return "play_media", 1.0

        # Lights & Switches
        # Matches: "turn on lights", "turn the piano lamp off", "turn off the lamp", "piano lamp off", "lights on", etc.
        if re.search(r"^(?:turn|switch|power|shut)\s+on\b", q) or re.search(r"^(?:turn|switch|power|shut)\s+.*?\bon\b", q) or re.search(r"\b(?:lights?|lamps?)\s+on$", q):
            return "turn_on", 1.0

        if re.search(r"^(?:turn|switch|power|shut)\s+off\b", q) or re.search(r"^(?:turn|switch|power|shut)\s+.*?\boff\b", q) or re.search(r"\b(?:lights?|lamps?)\s+off$", q):
            return "turn_off", 1.0

        # Storage & Indexing
        if re.search(r"^(?:index|reindex|scan)\s+(?:my\s+)?(?:storage|nextcloud|library|files)\b", q):
            return "index_storage", 1.0

        # HA Status / Health
        if q in ["sync home assistant", "refresh devices", "ha status", "sync ha"]:
            return "sync_ha", 1.0

        # 2. Semantic Routing (if active)
        if not self.is_active or not self.model or len(self.intent_embeddings) == 0 or np is None:
            # Keyword fallback when semantic router is not available
            turn_on_patterns = [r"turn\s+on", r"power\s+on", r"switch\s+on"]
            turn_off_patterns = [r"turn\s+off", r"power\s+off", r"switch\s+off"]
            for p in turn_on_patterns:
                if re.search(p, q):
                    return "turn_on", 0.8
            for p in turn_off_patterns:
                if re.search(p, q):
                    return "turn_off", 0.8
            log.debug(f"[FastPath] Semantic router not active, returning unknown (is_active={self.is_active}, model={self.model is not None}, embeddings={len(self.intent_embeddings) if np is not None else 0}, np={np is not None})")
            return "unknown", 0.0

        try:
            query_emb = next(iter(self.model.embed([query.lower()])))
            query_norm = np.linalg.norm(query_emb)
            if query_norm == 0:
                return "unknown", 0.0

            norms = np.linalg.norm(self.intent_embeddings, axis=1)
            dots = np.dot(self.intent_embeddings, query_emb)
            similarities = dots / (query_norm * norms + 1e-9)

            max_idx = np.argmax(similarities)
            best_score = float(similarities[max_idx])
            best_intent = self.intent_labels[max_idx]

            log.debug(f"[FastPath] Semantic match: intent='{best_intent}' score={best_score:.3f}")
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

