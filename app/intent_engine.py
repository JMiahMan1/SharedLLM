# app/intent_engine.py
import json
import os
import asyncio
import numpy as np
from typing import Dict, List, Optional, Tuple
from settings import log, GlobalResources, run_blocking

PHRASEBOOK_PATH = "/app/data/phrasebook.json"

# Default configuration if file is missing
DEFAULT_PHRASES = {
    "turn_on": [
        "turn on", "switch on", "enable", "activate", "power up", 
        "illuminate", "start", "lights on", "turn the lights on", "wake up"
    ],
    "turn_off": [
        "turn off", "switch off", "disable", "deactivate", "kill", 
        "shut down", "stop", "lights off", "cut the power", "go to sleep"
    ],
    "toggle": [
        "toggle", "flip", "change state", "switch state"
    ],
    "play_media": [
        "play", "resume", "start music", "play music", "play song", 
        "play album", "put on some music", "start the tv", "play track",
        "play artist", "queue up"
    ],
    "open_app": [
        "open", "launch", "start app", "go to app", "switch to", "watch"
    ],
    "stop_media": [
        "stop music", "pause", "quiet", "silence", "stop playback", 
        "pause music", "hush", "stop video", "pause video"
    ],
    "media_next": [
        "next track", "skip song", "next song", "skip", "next video"
    ],
    "media_previous": [
        "previous track", "previous song", "go back a song", "restart song"
    ],
    "nav_up": ["up", "go up", "move up", "scroll up"],
    "nav_down": ["down", "go down", "move down", "scroll down"],
    "nav_left": ["left", "go left", "move left"],
    "nav_right": ["right", "go right", "move right"],
    "nav_enter": ["select", "enter", "click", "ok", "choose", "go"],
    "nav_back": ["back", "go back", "return", "exit", "escape"],
    "nav_home": ["home", "go home", "menu", "main menu"],

    "calendar_add": [
        "schedule", "add event", "new appointment", "remind me", 
        "create event", "set a reminder", "book a meeting", "add to calendar"
    ],
    "calendar_list": [
        "list calendar", "show schedule", "what is on my calendar", 
        "check agenda", "list events", "what do i have today"
    ],
    "calendar_delete": [
        "delete event", "cancel meeting", "remove appointment", 
        "clear schedule", "cancel event"
    ],
    "calendar_update": [
        "reschedule", "move event", "change time", "postpone", 
        "move meeting", "update event"
    ],
    "time_query": [
        "what time is it", "current time", "clock", "tell me the time", 
        "what is the date", "what day is it"
    ],
    # --- New Timer Intents ---
    "timer_add": [
        "set a timer", "start a timer", "remind me in", "countdown", "timer for"
    ],
    "alarm_add": [
        "set an alarm", "wake me up", "alarm for", "wake up", "set alarm",
        "schedule alarm", "new alarm"
    ],
    "timer_delete": [
        "cancel timer", "stop timer", "delete alarm", "remove alarm",
        "cancel the alarm", "stop the timer"
    ],
    "timer_list": [
        "list timers", "show alarms", "what timers are running", 
        "check alarms", "my timers"
    ],
    "timer_pause": ["pause timer", "pause alarm"],
    "timer_resume": ["resume timer", "restart timer"],
    # -------------------------
    "intent_learn": [
        "learn that", "teach you", "remember that", "map phrase", 
        "training mode", "i want to teach you"
    ],
    "general_query": [
        "read a verse", "read the bible", "tell me a joke", 
        "who is", "what is", "explain", "tell me about", 
        "give me a quote", "recite"
    ],
    "content_query": [
        "read chapter", "summarize document", "find in my files", 
        "search my notes", "read from the book", "look up in nextcloud"
    ],
    "note_add": [
        "create note", "write a note", "new note", "take a note", "jot down",
        "make a note", "add note"
    ],
    "note_append": [
        "add to note", "append to note", "add to list", "add item to", 
        "put on my list", "add to shopping list"
    ],
    "note_read": [
        "read note", "check note", "what is in my note", "show note", "read list",
        "check shopping list"
    ]
}

class IntentEngine:
    def __init__(self):
        self.phrase_map: Dict[str, List[str]] = {}
        self.embeddings: Dict[str, np.ndarray] = {}
        self.vector_index: List[Tuple[str, str]] = []
        self.is_ready = False

    async def load(self):
        """Loads phrases from disk and pre-computes vectors."""
        log.info("--- Initializing Semantic Intent Engine ---")

        if os.path.exists(PHRASEBOOK_PATH):
            try:
                with open(PHRASEBOOK_PATH, "r") as f:
                    self.phrase_map = json.load(f)
                log.info(f"Loaded phrasebook from {PHRASEBOOK_PATH}")
            except Exception as e:
                log.error(f"Failed to load phrasebook: {e}. Using defaults.")
                self.phrase_map = DEFAULT_PHRASES
        else:
            log.info("No phrasebook found. Creating default.")
            self.phrase_map = DEFAULT_PHRASES
            await self.export()

        if not GlobalResources.embedding_model:
            log.warning("Embedding model not loaded. Intent Engine running in Keyword Mode only.")
            return

        try:
            all_texts = []
            self.vector_index = [] 
            
            for intent, phrases in self.phrase_map.items():
                for p in phrases:
                    all_texts.append(p)
                    self.vector_index.append((intent, p))
            
            if not all_texts: return

            log.info(f"Vectorizing {len(all_texts)} phrases for Intent Engine...")
            vectors = await run_blocking(lambda: GlobalResources.embedding_model.embed_documents(all_texts))
            self.embeddings = np.array(vectors) 
            self.is_ready = True
            log.info(f"Intent Engine Ready. Indexed {len(all_texts)} phrases.")
            
        except Exception as e:
            log.error(f"Error vectorizing phrasebook: {e}")

    # NOTE: Modified signature to include high_confidence_threshold and return is_high_confidence
    async def classify(self, query: str, threshold: float = 0.60, high_confidence_threshold: float = 0.80) -> Tuple[Optional[str], float, bool]:
        if not self.is_ready or not GlobalResources.embedding_model:
            # Assume keyword match is high confidence for simplicity if vectors fail
            return (self._keyword_fallback(query), 1.0, True)

        try:
            query_vec = await run_blocking(lambda: GlobalResources.embedding_model.embed_query(query))
            query_vec = np.array(query_vec)
            scores = np.dot(self.embeddings, query_vec)
            best_idx = np.argmax(scores)
            best_score = float(scores[best_idx])
            intent, matched_phrase = self.vector_index[best_idx]
            
            is_high_confidence = best_score >= high_confidence_threshold

            if best_score >= threshold:
                log.debug(f"Intent Match: '{query}' -> '{intent}' ({best_score:.2f}) via '{matched_phrase}'")
                return intent, best_score, is_high_confidence
            
            log.debug(f"Intent Low Confidence: '{query}' -> Best: '{intent}' ({best_score:.2f})")
            return None, best_score, is_high_confidence

        except Exception as e:
            log.error(f"Intent Classification Error: {e}")
            return None, 0.0, False

    def _keyword_fallback(self, query: str) -> Optional[str]:
        q = query.lower()
        for intent, phrases in self.phrase_map.items():
            for p in phrases:
                if p in q: return intent
        return None

    def get_valid_intents(self) -> List[str]:
        return list(self.phrase_map.keys())

    async def learn(self, phrase: str, intent: str):
        if intent not in self.phrase_map: return False
        phrase = phrase.lower().strip()
        if phrase not in self.phrase_map[intent]:
            self.phrase_map[intent].append(phrase)
            await self.export() 
            await self.load() 
            return True
        return True 

    async def export(self):
        try:
            os.makedirs(os.path.dirname(PHRASEBOOK_PATH), exist_ok=True)
            with open(PHRASEBOOK_PATH, "w") as f:
                json.dump(self.phrase_map, f, indent=2)
            return True
        except Exception as e:
            log.error(f"Export failed: {e}")
            return False

# Global Instance
engine = IntentEngine()
