#!/usr/bin/env python3
"""
Pipeline Profiling Tool
Measures time spent in each stage of request processing
"""
import asyncio
import time
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

RAG_API_URL = os.getenv("RAG_API_URL")
if not RAG_API_URL:
    # Fallback to constructing from RAG_ADDRESS if available
    addr = os.getenv("RAG_ADDRESS")
    if addr:
        RAG_API_URL = f"http://{addr}:11435/api/chat"
    else:
        print("ERROR: RAG_API_URL or RAG_ADDRESS not set in .env")
        sys.exit(1)

async def profile_request(query: str):
    """Profile a single request to identify bottlenecks"""
    print(f"\nProfiling request: '{query}'")
    print("=" * 60)
    
    # Add timing headers if the API supports it
    # For now, we'll measure total time and check server logs
    
    start = time.time()
    try:
        r = requests.post(
            RAG_API_URL,
            json={"query": query, "user_id": "profile_user"},
            timeout=120
        )
        elapsed = time.time() - start
        
        if r.status_code == 200:
            response = r.json()
            print(f"[OK] Request completed in {elapsed:.2f}s")
            print(f"   Response: {response.get('response', '')[:100]}")
            
            # Analyze timing
            if elapsed > 30:
                print(f"\n[WARN] SLOW REQUEST: {elapsed:.2f}s")
                print("   Possible causes:")
                print("   - Multiple Ollama calls (contextualize + orchestrator)")
                print("   - Intent classification happening multiple times")
                print("   - Model loading/cold start")
                print("   - Network latency to Ollama")
            elif elapsed > 10:
                print(f"\n[WARN] MODERATE DELAY: {elapsed:.2f}s")
            else:
                print(f"\n[OK] Acceptable response time: {elapsed:.2f}s")
        else:
            print(f"[FAIL] Request failed: {r.status_code}")
            print(f"   Response: {r.text[:200]}")
            
    except requests.exceptions.Timeout:
        elapsed = time.time() - start
        print(f"[FAIL] Request timed out after {elapsed:.2f}s")
    except Exception as e:
        elapsed = time.time() - start
        print(f"[FAIL] Error after {elapsed:.2f}s: {e}")


def main():
    print("=" * 60)
    print("Pipeline Profiling Tool")
    print("=" * 60)
    
    # Test queries of varying complexity
    test_queries = [
        "test",  # Simple query
        "turn on the lights",  # Simple command
        "set the volume on Office Speaker to 30%",  # Volume command
    ]
    
    for query in test_queries:
        asyncio.run(profile_request(query))
        time.sleep(2)  # Brief pause between requests
    
    print("\n" + "=" * 60)
    print("Recommendations")
    print("=" * 60)
    print("""
To get detailed timing breakdown, check server logs:
  ssh jeremiah@192.168.2.211
  docker logs --tail 100 unified_rag_api | grep -E "\[PIPELINE|\[INTENT|Ollama"

Look for:
  - Multiple intent classification calls
  - Sequential Ollama calls (contextualize + orchestrator)
  - Slow model responses
    """)


if __name__ == "__main__":
    main()

