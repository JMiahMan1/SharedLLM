import re
from typing import Tuple, Optional
from app.settings import log, ACTION_TOOL_CONFIDENCE_THRESHOLD
from app.intent_engine import engine as intent_engine
from app.domains.media import REGEX_INTENT_MAP

class IntentClassifier:
    """
    Handles intent detection strategy:
    1. Regex Overrides (Highest priority, deterministic)
    2. Vector/LLM Classification (Probabilistic)
    """

    @staticmethod
    def apply_regex_override(query: str) -> Optional[str]:
        log.info(f"[REGEX START] apply_regex_override called with: '{query}'")
        q_low = query.lower()
        log.info(f"[REGEX CHECK] Checking query: '{q_low}'")
        log.info(f"[REGEX CHECK] REGEX_INTENT_MAP has {len(REGEX_INTENT_MAP)} patterns")
        for pattern, intent in REGEX_INTENT_MAP.items():
            log.info(f"[REGEX CHECK] Testing pattern: {pattern} -> {intent}")
            if re.search(pattern, q_low):
                log.info(f"[REGEX OVERRIDE] Matched '{intent}' via pattern: {pattern[:50]}...")
                return intent
        log.info(f"[REGEX CHECK] No regex matches for: '{q_low}'")
        return None

    @classmethod
    async def get_intent(cls, query: str) -> Tuple[str, float, bool]:
        """
        Returns (intent, score, is_high_confidence)
        """
        log.info(f"[INTENT] get_intent called with query: '{query}'")

        # 1. Regex
        regex_intent = cls.apply_regex_override(query)
        if regex_intent:
            log.info(f"[INTENT] Regex override: '{query}' -> {regex_intent}")
            return regex_intent, 1.0, True

        # 2. Vector Engine
        log.info(f"[INTENT] Falling back to vector engine for: '{query}'")
        result = await intent_engine.classify(
            query,
            threshold=ACTION_TOOL_CONFIDENCE_THRESHOLD,
            high_confidence_threshold=0.85
        )
        log.info(f"[INTENT] Vector result: {result}")
        return result
