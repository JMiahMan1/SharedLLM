#!/usr/bin/env python3
"""
Connectivity Diagnostic Tool
Tests connectivity between RAG server and Ollama, Home Assistant, etc.
"""
import os
import sys
import requests
import time
from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL")
if not OLLAMA_URL:
    # Try local fallback only if explicitly allowed, otherwise stricter
    # User said NO env info hardcoded. So "localhost" is maybe okay but IP isn't.
    OLLAMA_URL = "http://localhost:11434" 

HA_URL = os.getenv("HA_URL")
RAG_API_URL = os.getenv("RAG_API_URL")

if not RAG_API_URL:
    addr = os.getenv("RAG_ADDRESS")
    if addr:
        RAG_API_URL = f"http://{addr}:11435/api/chat"

def test_ollama_connectivity():
    """Test if Ollama is reachable"""
    print(f"\nTesting Ollama Connectivity: {OLLAMA_URL}")
    print("-" * 60)
    
    try:
        # Test 1: Basic connectivity
        print("1. Testing basic connectivity...")
        start = time.time()
        r = requests.get(f"{OLLAMA_URL.rstrip('/')}/api/tags", timeout=5)
        elapsed = time.time() - start
        print(f"   [OK] Connected in {elapsed:.2f}s (Status: {r.status_code})")
        
        # Test 2: Version check
        print("2. Checking Ollama version...")
        r = requests.get(f"{OLLAMA_URL.rstrip('/')}/api/version", timeout=5)
        if r.status_code == 200:
            version = r.json()
            print(f"   [OK] Version: {version.get('version', 'unknown')}")
        
        # Test 3: Model availability
        print("3. Checking available models...")
        r = requests.get(f"{OLLAMA_URL.rstrip('/')}/api/tags", timeout=5)
        if r.status_code == 200:
            models = r.json().get("models", [])
            model_names = [m.get("name", "unknown") for m in models]
            print(f"   [OK] Found {len(model_names)} models: {', '.join(model_names[:5])}")
            if len(model_names) > 5:
                print(f"      ... and {len(model_names) - 5} more")
        
        # Test 4: Check if model is loaded
        print("4. Checking if model is loaded...")
        default_model = os.getenv("DEFAULT_MODEL", "qwen2.5:latest")
        r = requests.get(f"{OLLAMA_URL.rstrip('/')}/api/ps", timeout=5)
        if r.status_code == 200:
            running = r.json().get("models", [])
            loaded_models = [m.get("name", "") for m in running]
            if default_model in loaded_models:
                print(f"   [OK] Model '{default_model}' is already loaded")
            else:
                print(f"   [WARN] Model '{default_model}' needs to be loaded (first request will be slower)")
        
        # Test 5: Generate test (with longer timeout for cold start)
        print("5. Testing generation (may take time if model needs loading)...")
        default_model = os.getenv("DEFAULT_MODEL", "qwen2.5:latest")
        payload = {
            "model": default_model,
            "prompt": "Say 'OK'",
            "stream": False,
            "options": {"num_predict": 5}
        }
        start = time.time()
        try:
            r = requests.post(
                f"{OLLAMA_URL.rstrip('/')}/api/generate",
                json=payload,
                timeout=60  # Longer timeout for cold start
            )
            elapsed = time.time() - start
            if r.status_code == 200:
                response = r.json().get("response", "")
                print(f"   [OK] Generated response in {elapsed:.2f}s: '{response[:50]}'")
            else:
                print(f"   [FAIL] Generation failed: {r.status_code}")
        except requests.exceptions.Timeout:
            elapsed = time.time() - start
            print(f"   [FAIL] TIMEOUT after {elapsed:.2f}s: Model may be loading or stuck")
            print(f"      This is likely the root cause of your timeout issues!")
            return False
        
        return True
        
    except requests.exceptions.Timeout:
        print(f"   [FAIL] TIMEOUT: Ollama at {OLLAMA_URL} is not reachable")
        print(f"      This is likely the root cause of your timeout issues!")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"   [FAIL] CONNECTION ERROR: Cannot connect to {OLLAMA_URL}")
        print(f"      Error: {e}")
        return False
    except Exception as e:
        print(f"   [FAIL] ERROR: {type(e).__name__}: {e}")
        return False


def test_ha_connectivity():
    """Test if Home Assistant is reachable"""
    if not HA_URL:
        print("\n[WARN] HA_URL not configured, skipping Home Assistant test")
        return None
    
    print(f"\nTesting Home Assistant Connectivity: {HA_URL}")
    print("-" * 60)
    
    try:
        ha_token = os.getenv("HA_TOKEN")
        if not ha_token:
            print("   [WARN] HA_TOKEN not set, cannot test authenticated endpoints")
            return None
        
        headers = {"Authorization": f"Bearer {ha_token}"}
        
        # Test 1: Basic connectivity
        print("1. Testing basic connectivity...")
        start = time.time()
        r = requests.get(f"{HA_URL.rstrip('/')}/api/", headers=headers, timeout=5)
        elapsed = time.time() - start
        print(f"   [OK] Connected in {elapsed:.2f}s (Status: {r.status_code})")
        
        # Test 2: Config check
        print("2. Checking Home Assistant config...")
        r = requests.get(f"{HA_URL.rstrip('/')}/api/config", headers=headers, timeout=5)
        if r.status_code == 200:
            config = r.json()
            print(f"   [OK] HA Version: {config.get('version', 'unknown')}")
            print(f"   [OK] Location: {config.get('location_name', 'unknown')}")
        
        return True
        
    except requests.exceptions.Timeout:
        print(f"   [FAIL] TIMEOUT: Home Assistant at {HA_URL} is not reachable")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"   [FAIL] CONNECTION ERROR: Cannot connect to {HA_URL}")
        print(f"      Error: {e}")
        return False
    except Exception as e:
        print(f"   [FAIL] ERROR: {type(e).__name__}: {e}")
        return False


def test_rag_api():
    """Test if RAG API is responding"""
    print(f"\nTesting RAG API Connectivity: {RAG_API_URL}")
    print("-" * 60)
    
    try:
        # Test 1: Basic connectivity
        print("1. Testing basic connectivity...")
        start = time.time()
        payload = {
            "query": "test",
            "user_id": "diagnostic_user"
        }
        r = requests.post(RAG_API_URL, json=payload, timeout=30)
        elapsed = time.time() - start
        
        if r.status_code == 200:
            print(f"   [OK] Connected in {elapsed:.2f}s (Status: {r.status_code})")
            response = r.json()
            print(f"   [OK] Response received: {response.get('response', '')[:100]}")
            return True
        else:
            print(f"   [WARN] Status: {r.status_code}")
            print(f"   Response: {r.text[:200]}")
            return False
            
    except requests.exceptions.Timeout:
        print(f"   [FAIL] TIMEOUT: RAG API at {RAG_API_URL} timed out after 30s")
        print(f"      This suggests Ollama connectivity issues downstream")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"   [FAIL] CONNECTION ERROR: Cannot connect to {RAG_API_URL}")
        print(f"      Error: {e}")
        return False
    except Exception as e:
        print(f"   [FAIL] ERROR: {type(e).__name__}: {e}")
        return False


def main():
    print("=" * 60)
    print("Connectivity Diagnostic Tool")
    print("=" * 60)
    
    results = {
        "ollama": test_ollama_connectivity(),
        "home_assistant": test_ha_connectivity(),
        "rag_api": test_rag_api()
    }
    
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    
    for service, result in results.items():
        if result is None:
            status = "[SKIPPED]"
        elif result:
            status = "[PASS]"
        else:
            status = "[FAIL]"
        print(f"{service.upper():20} {status}")
    
    # Recommendations
    print("\n" + "=" * 60)
    print("Recommendations")
    print("=" * 60)
    
    if not results.get("ollama"):
        print("""
[CRITICAL] Ollama connectivity failed!

This is likely the root cause of your timeout issues. The RAG server
cannot reach Ollama to process requests.

Solutions:
1. Verify Ollama is running at: {OLLAMA_URL}
2. Check network connectivity between RAG server (192.168.2.205) and Ollama (192.168.1.161)
3. If Ollama moved to a new server, update OLLAMA_URL in .env
4. Test from RAG server directly:
   ssh jeremiah@192.168.2.205
   curl http://192.168.1.161:11434/api/tags
        """.format(OLLAMA_URL=OLLAMA_URL))
    
    if not results.get("rag_api") and results.get("ollama"):
        print("""
[WARN] RAG API timeout but Ollama is reachable.

This suggests the issue is in the request processing pipeline.
Check server logs for more details.
        """)
    
    if all(results.values()):
        print("[OK] All connectivity tests passed!")
        print("   If you're still experiencing timeouts, check:")
        print("   - Server logs for processing delays")
        print("   - Model loading times")
        print("   - Request complexity")


if __name__ == "__main__":
    main()

