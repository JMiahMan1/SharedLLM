import httpx
import asyncio
import time
import json
import os
import re

def get_config_url():
    """Parses .env and docker-compose.yml to find the real physical Ollama URL."""
    ollama_url = "http://localhost:11434" # Default fallback
    
    # 1. Try to get from .env
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            content = f.read()
            match = re.search(r"OLLAMA_URL=(.+)", content)
            if match:
                ollama_url = match.group(1).strip()
    
    # 2. Try to resolve alias from docker-compose.yml
    if os.path.exists("docker-compose.yml") and "://" in ollama_url:
        protocol, rest = ollama_url.split("://", 1)
        host_port = rest.split("/", 1)[0]
        host = host_port.split(":")[0]
        port = host_port.split(":")[1] if ":" in host_port else "11434"
        
        with open("docker-compose.yml", "r") as f:
            compose_content = f.read()
            # Look for extra_hosts mapping: "alias:ip"
            host_match = re.search(fr'"{host}:([\d\.]+)"', compose_content)
            if host_match:
                real_ip = host_match.group(1)
                ollama_url = f"{protocol}://{real_ip}:{port}"
                print(f"Resolved {host} to physical IP {real_ip} via extra_hosts")
    
    return ollama_url

async def test_model_direct(client, url, model_name):
    print(f"Testing model: {model_name}...")
    start_time = time.time()
    try:
        response = await client.post(
            f"{url}/api/generate",
            json={
                "model": model_name,
                "prompt": "Identify yourself and confirm you can hear me. Keep it short.",
                "stream": False
            },
            timeout=60.0
        )
        latency = time.time() - start_time
        if response.status_code == 200:
            resp_text = response.json().get("response", "").strip()
            print(f"  SUCCESS: {model_name} responded in {latency:.2f}s: \"{resp_text}\"")
            return {"model": model_name, "success": True, "latency": latency, "response": resp_text}
        else:
            print(f"  FAILED: {model_name} returned status {response.status_code}")
            return {"model": model_name, "success": False, "error": response.text}
    except Exception as e:
        print(f"  ERROR: {model_name} failed with {str(e)}")
        return {"model": model_name, "success": False, "error": str(e)}

async def main():
    target_url = get_config_url()
    print(f"Direct Diagnostic Target: {target_url}")
    
    async with httpx.AsyncClient() as client:
        # 1. Get models from the physical IP
        try:
            resp = await client.get(f"{target_url}/api/tags")
            models = [m['name'] for m in resp.json().get('models', [])]
            print(f"Found {len(models)} models: {models}")
        except Exception as e:
            print(f"Could not connect to Ollama at {target_url}: {e}")
            return

        results = []
        for model in models:
            res = await test_model_direct(client, target_url, model)
            results.append(res)

        os.makedirs("data", exist_ok=True)
        with open("data/ollama_direct_results.json", "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nDirect testing complete. Results saved to data/ollama_direct_results.json")

if __name__ == "__main__":
    asyncio.run(main())
