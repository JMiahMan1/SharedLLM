import re
from typing import Tuple, Optional
from app.settings import log, ACTION_TOOL_CONFIDENCE_THRESHOLD
from app.intent_engine import engine as intent_engine
from app.logic.media_ops import REGEX_INTENT_MAP

class IntentClassifier:
    """
    Handles intent detection strategy:
    1. Regex Overrides (Highest priority, deterministic)
    2. Vector/LLM Classification (Probabilistic)
    """

    @staticmethod
    def apply_regex_override(query: str) -> Optional[str]:
        q_low = query.lower()
        for pattern, intent in REGEX_INTENT_MAP.items():
            if re.search(pattern, q_low):
                log.debug(f"[REGEX OVERRIDE] Matched '{intent}' via pattern: {pattern[:50]}...")
                return intent
        return None

    @classmethod
    async def get_intent(cls, query: str) -> Tuple[str, float, bool]:
        """
        Returns (intent, score, is_high_confidence)
        """
        # 1. Regex
        regex_intent = cls.apply_regex_override(query)
        if regex_intent:
            log.info(f"[INTENT] Regex override: '{query}' -> {regex_intent}")
            return regex_intent, 1.0, True
        
        # 2. Vector Engine
        return await intent_engine.classify(
            query, 
            threshold=ACTION_TOOL_CONFIDENCE_THRESHOLD,
            high_confidence_threshold=0.85 
        )
