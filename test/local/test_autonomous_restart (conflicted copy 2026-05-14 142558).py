import asyncio
import os
import sys

import httpx

async def test_control_plane_integration():
    """
    Tests the full Control Plane integration: listing services, checking models, and restarting.
    """
    base_url = os.getenv("LIVE_TEST_URL", "http://127.0.0.1:8002")
    gateway_url = f"{base_url}/api" if base_url != "http://127.0.0.1:8002" else "http://127.0.0.1:8002/api"
    headers = {"X-Internal-Secret": os.getenv("INTERNAL_SECRET")}
    
    print(f"=== Testing SharedLLM Integration against {base_url} ===\n")

    with httpx.Client(timeout=30.0) as client:
        # 1. Test Model Listing
        print("1. Testing Model Listing (/api/models)...")
        try:
            resp = client.get(f"{gateway_url}/models", headers=headers)
            if resp.status_code == 200:
                print("✅ Models Found:", resp.json().get("models"))
            else:
                print(f"❌ Models Listing Failed: {resp.status_code}")
        except Exception as e:
            print(f"❌ Models Listing Error: {e}")

        print("\n2. Testing Service Listing (/api/admin/services)...")
        try:
            resp = client.get(f"{gateway_url}/admin/services", headers=headers)
            if resp.status_code == 200:
                containers = resp.json()
                print(f"✅ Found {len(containers)} containers:")
                for c in containers:
                    print(f"   - {c['name']}: {c['status']}")
            else:
                print(f"❌ Service Listing Failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"❌ Service Listing Error: {e}")

        print("\n3. Testing Service Restart (/api/admin/services/.../restart)...")
        admin_endpoint = f"{gateway_url}/admin/services/sharedllm_execution/restart"
        try:
            response = client.post(admin_endpoint, headers=headers)
            if response.status_code == 200:
                print("✅ Control Plane Restart Test SUCCESS!")
                print("Response:", response.json())
            else:
                print(f"❌ Control Plane Restart Test FAILED: {response.status_code}")
                print("Response:", response.text)
        except Exception as e:
            print(f"❌ Control Plane Restart Test FAILED: Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_control_plane_integration())
