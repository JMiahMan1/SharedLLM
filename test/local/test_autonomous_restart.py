import asyncio
import os
import sys

import httpx

async def test_control_plane_request():
    """
    Simulates a Raven tool execution outputting a ControlPlaneRequest.
    We test if the Gateway correctly proxies this tool execution to the control_plane.
    """
    base_url = os.getenv("LIVE_TEST_URL", "http://127.0.0.1:8002")
    gateway_url = f"{base_url}/api" if base_url != "http://127.0.0.1:8002" else "http://127.0.0.1:8002/api"
    
    print(f"Testing ControlPlaneRequest tool execution against {gateway_url}...")

    admin_endpoint = f"{gateway_url}/admin/services/sharedllm_execution/restart"
    print(f"Calling Admin proxy endpoint: {admin_endpoint}")
    
    headers = {"X-Internal-Secret": os.getenv("INTERNAL_SECRET", "change-me-in-production")}
    
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(admin_endpoint, headers=headers)
            print("Response Code:", response.status_code)
            
            if response.status_code == 200:
                print("✅ Control Plane Restart Test SUCCESS!")
                print("Response:", response.json())
            elif response.status_code in (401, 403):
                print("❌ Control Plane Restart Test FAILED: Unauthorized. Ensure you are passing the correct Admin credentials or Internal Secret.")
                print("Response:", response.text)
                sys.exit(1)
            else:
                print(f"❌ Control Plane Restart Test FAILED: {response.status_code}")
                print("Response:", response.text)
                sys.exit(1)
    except Exception as e:
        print(f"❌ Control Plane Restart Test FAILED: Exception: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_control_plane_request())
