"""Intent classifier module.

Provides IntentClassifier for categorizing user queries into intents.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("app.logic.intents.classifier")

# Default intent patterns (keyword-based fallback when LLM is unavailable)
_INTENT_PATTERNS: Dict[str, List[str]] = {
    "media_play": ["play", "watch", "start", "launch"],
    "media_pause": ["pause", "stop", "hold"],
    "media_volume": ["volume", "louder", "quieter", "mute"],
    "ha_service": ["turn", "switch", "toggle", "lock", "unlock", "open", "close"],
    "timer_set": ["set", "timer", "alarm", "remind"],
    "search": ["search", "find", "look up", "search for"],
    "web_search": ["web search", "google", "bing", "search the web"],
    "storage_read": ["read", "open", "fetch", "get file"],
    "storage_write": ["write", "save", "create", "update"],
    "tts": ["speak", "say", "announce", "tell me"],
    "unknown": [],
}


class IntentClassifier:
    """
    Classifies user queries into intents.

    Supports both pattern-based (keyword) matching and LLM-based classification.
    Falls back to pattern matching when the LLM is unavailable.
    """

    def __init__(self, llm_client: Optional[Any] = None) -> None:
        self._llm = llm_client

    def classify(self, query: str, top_k: int = 1) -> Tuple[str, float]:
        """
        Classify a query into an intent.

        Returns:
            Tuple of (intent_name, confidence_score).
        """
        if self._llm:
            return self._classify_with_llm(query, top_k)

        return self._classify_patterns(query)

    def _classify_patterns(self, query: str) -> Tuple[str, float]:
        """Pattern-based classification using keyword matching."""
        query_lower = query.lower()
        scores: List[Tuple[str, float]] = []

        for intent, keywords in _INTENT_PATTERNS.items():
            if not keywords:
                continue
            matches = sum(1 for kw in keywords if kw in query_lower)
            if matches > 0:
                confidence = min(matches / len(keywords), 1.0) * 0.85
                scores.append((intent, confidence))

        if not scores:
            return ("unknown", 0.0)

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[0]

    def _classify_with_llm(self, query: str, top_k: int = 1) -> Tuple[str, float]:
        """LLM-based classification."""
        try:
            if self._llm is None:
                raise RuntimeError("LLM client is None")
            result = self._llm.classify_intent(query, top_k=top_k)
            if isinstance(result, dict):
                return (
                    result.get("intent", "unknown"),
                    result.get("confidence", 0.0),
                )
            if isinstance(result, (list, tuple)) and len(result) >= 2:
                return (str(result[0]), float(result[1]))
        except Exception as e:
            log.warning(f"LLM classification failed, falling back to patterns: {e}")

        return self._classify_patterns(query)
