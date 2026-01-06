#!/usr/bin/env python3
"""
Quick test script to verify source filtering logic
"""
import requests

BASE_URL = "http://192.168.2.211:11435"

print("=" * 60)
print("Testing RAG Search Source Filtering")
print("=" * 60)

# Test 1: No source parameter (should return both)
print("\n1. No source parameter (should return both HA + Nextcloud):")
r = requests.get(f"{BASE_URL}/api/rag/search", params={"q": "office tv", "k": 3})
data = r.json()
sources = [res["source"] for res in data["results"]]
print(f"   Sources returned: {set(sources)}")
print(f"   HA count: {sources.count('home_assistant')}")
print(f"   NC count: {sources.count('nextcloud')}")

# Test 2: source=ha (should return ONLY HA)
print("\n2. source=ha (should return ONLY Home Assistant):")
r = requests.get(f"{BASE_URL}/api/rag/search", params={"q": "office tv", "k": 3, "source": "ha"})
data = r.json()
sources = [res["source"] for res in data["results"]]
print(f"   Sources returned: {set(sources)}")
print(f"   HA count: {sources.count('home_assistant')}")
print(f"   NC count: {sources.count('nextcloud')}")
if sources.count('nextcloud') > 0:
    print("   ❌ BUG: Nextcloud results present when source=ha!")
else:
    print("   ✅ PASS: Only HA results")

# Test 3: source=nextcloud (should return ONLY NC)
print("\n3. source=nextcloud (should return ONLY Nextcloud):")
r = requests.get(f"{BASE_URL}/api/rag/search", params={"q": "office", "k": 3, "source": "nextcloud"})
data = r.json()
sources = [res["source"] for res in data["results"]]
print(f"   Sources returned: {set(sources)}")
print(f"   HA count: {sources.count('home_assistant')}")
print(f"   NC count: {sources.count('nextcloud')}")
if sources.count('home_assistant') > 0:
    print("   ❌ BUG: HA results present when source=nextcloud!")
else:
    print("   ✅ PASS: Only NC results")

print("\n" + "=" * 60)
