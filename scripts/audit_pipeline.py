
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), 'services/gateway'))
# pyright: ignore[reportMissingImports]
from intent_engine import IntentEngine  # pyright: ignore[reportMissingImports]


def test_intent():
    print("--- 1. Testing Intent Engine ---")
    engine = IntentEngine()
    engine.load()

    query = "Execute the StorageIndexRequest tool for the path /Notes"
    intent, conf = engine.classify(query)
    print(f"Query: '{query}'")
    print(f"Intent Classified: {intent} (Confidence: {conf})")
    print(f"Bypass LLM? {engine.should_bypass_llm(conf)}")
    return intent

test_intent()
