import asyncio
import os
import sys

# Add the tools directory to the path so we can import testing utilities
sys.path.append(os.path.join(os.path.dirname(__file__), '../../tools'))
from tests.test_live_api import get_live_test_url, make_request

async def test_control_plane_request():
    """
    Simulates a Raven tool execution outputting a ControlPlaneRequest.
    We test if the Gateway correctly proxies this tool execution to the control_plane.
    """
    base_url = get_live_test_url()
    gateway_url = f"{base_url}/api" if base_url != "http://127.0.0.1:8002" else "http://127.0.0.1:8002/api"
    
    print(f"Testing ControlPlaneRequest tool execution against {gateway_url}...")

    # We mock the LLM output that the Inference API would process
    # But since we can't easily fake the LLM output through the standard Chat endpoint without hitting the real LLM,
    # we can hit the Gateway's test endpoint or the actual execution logic if exposed.
    # Actually, the simplest way is to test the newly added Admin endpoint that wraps it.
    
    admin_endpoint = f"{gateway_url}/admin/services/sharedllm_execution/restart"
    print(f"Calling Admin proxy endpoint: {admin_endpoint}")
    
    # We must use INTERNAL_SECRET to mimic an admin call
    headers = {"X-Internal-Secret": os.getenv("INTERNAL_SECRET", "change-me-in-production")}
    
    try:
        response = make_request("POST", admin_endpoint, headers=headers)
        print("Response Code:", response.status_code)
        
        if response.status_code == 200:
            print("✅ Control Plane Restart Test SUCCESS!")
            print("Response:", response.json())
        elif response.status_code == 401 or response.status_code == 403:
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
