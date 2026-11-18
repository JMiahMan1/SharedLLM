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


#: What "turn on" / "turn off" may switch. Never an automation, a script, a camera,
#: a firmware update or a sensor, however close its name.
ON_OFF_DOMAINS = frozenset({"light", "switch", "fan", "input_boolean", "media_player", "humidifier", "climate", "siren"})
#: A device is chosen only above this score and this far ahead of the next one.
DEVICE_MATCH_MIN = 0.70
DEVICE_MATCH_MARGIN = 0.04
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9", "ten": "10",
}
#: Heard for a number only as the last word ("kitchen light to"): elsewhere they are words.
_TRAILING_NUMBER_WORDS = {"won": "1", "to": "2", "too": "2", "for": "4", "fore": "4", "ate": "8"}
_FILLER = {"the", "a", "an", "my", "our", "please", "now"}


def _strip_question(target: str) -> str:
    """'is the hall lamp' -> 'hall lamp' (a state question reaches the same patterns)."""
    return re.sub(r"^(?:is|are|was|were|did|do|does)\s+", "", target.strip(), flags=re.IGNORECASE)


def _norm_name(text: str, numbers: bool = False) -> str:
    """Lower case, separators to spaces, filler dropped, plurals singular; spoken numbers as digits."""
    words = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()
    out = []
    for i, w in enumerate(words):
        if w in _FILLER:
            continue
        if numbers:
            if w in _NUMBER_WORDS:
                w = _NUMBER_WORDS[w]
            elif i == len(words) - 1 and w in _TRAILING_NUMBER_WORDS and out:
                w = _TRAILING_NUMBER_WORDS[w]
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        # Hub names often repeat the device in the entity ("Security-Light Security Light").
        if w not in out:
            out.append(w)
    return " ".join(out)


def _sound(word: str) -> str:
    """A rough sound of an English word: spellings that sound alike come out the same.

    "lite"/"light", "hole"/"hall", "fone"/"phone". Silent and soft letters are
    folded, then vowels after the first letter dropped and doubles collapsed.
    """
    w = word.lower()
    for a, b in (("ght", "t"), ("ph", "f"), ("ck", "k"), ("qu", "kw"), ("wh", "w"), ("kn", "n"), ("wr", "r")):
        w = w.replace(a, b)
    w = re.sub(r"c(?=[eiy])", "s", w).replace("c", "k")
    if not w:
        return ""
    tail = re.sub(r"[aeiouyhw]", "", w[1:])
    out = w[0]
    for ch in tail:
        if ch != out[-1]:
            out += ch
    return out


def _word_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a.isdigit() or b.isdigit():
        return 0.0  # "light 2" is never "light 4"
    sim = difflib.SequenceMatcher(None, a, b).ratio()
    if len(a) >= 3 and len(b) >= 3 and _sound(a) == _sound(b):
        sim = max(sim, 0.85)  # heard as a word that sounds the same
    # "hallway" for "hall": one word is the other with a longer ending.
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
        sim = max(sim, 0.8)
    # A word heard without its first sound ("amp" for "lamp", "ight" for "light").
    if len(a) >= 3 and len(b) >= 3 and (b.endswith(a) or a.endswith(b)) and abs(len(a) - len(b)) <= 2:
        sim = max(sim, 0.85)
    return sim


def _name_score(spoken: str, name: str) -> float:
    """How well a spoken name fits a device name, 0..1."""
    if spoken == name:
        return 1.0
    said, named = spoken.split(), name.split()
    if not said or not named:
        return 0.0
    whole = difflib.SequenceMatcher(None, spoken, name).ratio()
    best_per_said = [max(_word_similarity(w, n) for n in named) for w in said]
    words = sum(best_per_said) / len(best_per_said)
    # Of the device's own words, how many were said (a lone "garage" is not "garage door state").
    covered = sum(1 for n in set(named) if max(_word_similarity(w, n) for w in said) >= 0.75) / len(set(named))
    if covered < 0.6:
        return 0.0  # too little of the device's name was said to be sure it is this one
    return 0.4 * whole + 0.4 * words + 0.2 * covered


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
        """Updates the local cache of friendly names for fuzzy matching.

        An entity the hub hides (``attributes._hidden``, a duplicate device's copy) never enters it: the
        fast path must not pick the hidden copy of a bulb by its name. A command naming it still works.
        """
        entities = [e for e in entities if isinstance(e, dict)
                    and not (isinstance(e.get("attributes"), dict) and e["attributes"].get("_hidden"))]
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
        records = []
        for ent in entities:
            eid = str(ent.get("entity_id") or "")
            if "." not in eid:
                continue
            fname = str((ent.get("attributes") or {}).get("friendly_name") or "")
            names = {n for n in (_norm_name(fname), _norm_name(eid.split(".", 1)[1])) if n}
            records.append((eid, eid.split(".", 1)[0], tuple(names), str(ent.get("state") or "").lower()))
        self.device_records = records
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
        target = self.extract_target(query, intent)
        if not target:
            return None

        if intent in ("turn_on", "turn_off"):
            return self.resolve_device(target)
        return self._fuzzy_entity(target)

    def extract_target(self, query: str, intent: str) -> str | None:
        """The device as said ("the office tv"), from the command's wording; None when none is named."""
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

        return target or None

    #: The state word that ends a yes/no question about a device ("... on?"), as heard:
    #: speech to text sometimes adds an s ("Is the hall lamp ons").
    _STATE_WORDS = r"(?:on|ons|off|offs|still on|still off|turned on|turned off|running|playing)"

    def state_question_target(self, query: str) -> str | None:
        """The device a yes/no state question names: "Is the hall lamp on?" -> "hall lamp".

        None when the question does not end in a device state word, so "is it going
        to rain" names no device.
        """
        q = (query or "").lower().strip().strip("?.!").strip()
        match = re.match(
            r"^(?:is|are|was|were)\s+(?:the\s+|my\s+|our\s+)?(.+?)\s+" + self._STATE_WORDS
            + r"(?:\s+(?:right now|now|currently))?$",
            q,
        )
        return match.group(1).strip() if match else None

    def _fuzzy_entity(self, target: str) -> str | None:
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

    def resolve_device(self, target: str) -> str | None:
        """The switchable device a (possibly misheard) spoken name means, or None.

        Speech-to-text drops and swaps sounds ("hall amp", "security lite",
        "kitchen light to"), and device names are messy ("Hall-Lamp",
        "office_lamp", a brand and model after the room). Every device that can be
        switched on and off is scored on its whole name, word by word (a word
        heard without its first letter or with a close spelling still counts) and
        on how much of its name was said. Only a clear winner is returned: a weak
        or tied match goes to the model, which can ask, rather than switching the
        wrong thing. Before this, any entity could match, and "turn on the string
        lights" switched on an automation.
        """
        spoken = _norm_name(_strip_question(target), numbers=True)
        if not spoken:
            return None
        scored: dict[str, float] = {}
        for eid, domain, names, state in getattr(self, "device_records", []):
            if domain not in ON_OFF_DOMAINS:
                continue
            best = max((_name_score(spoken, name) for name in names), default=0.0)
            if state in ("unavailable", "unknown"):
                best -= 0.05  # of two devices with one name, the one that answers
            if best > scored.get(eid, 0.0):
                scored[eid] = best
        if not scored:
            log.warning(f"[FastPath] No switchable device for '{target}'")
            return None
        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
        best_id, best = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0.0
        if best < DEVICE_MATCH_MIN or best - runner < DEVICE_MATCH_MARGIN:
            log.warning(
                f"[FastPath] No clear device for '{target}' (best {best_id} {best:.2f}, next {runner:.2f}); the model decides"
            )
            return None
        log.info(f"[FastPath] Resolved '{target}' to '{best_id}' (score {best:.2f}, next {runner:.2f})")
        return best_id

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
            if (len(parts) > 1 and (parts[0] in parts[1] or parts[1] in parts[0])) or (len(parts) > 1 and any(w in parts[0] for w in ["turn", "play", "pause", "resume", "stop", "switch", "lights", "light", "skip", "next"])):
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
        if re.search(r"^(?:play|listen to|put on|stream|start playing)\b", q) and not re.search(r"^(?:play\s+(?:a\s+game|roles|dumb))\b", q):
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

