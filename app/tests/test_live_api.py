# app/tests/test_live_api.py
import argparse
import asyncio
import aiohttp
import json
import time
from pprint import pprint

async def test_live_chat(base_url, query, token):
    url = f"{base_url}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": query}],
        "stream": False
    }
    
    print(f"[{time.strftime('%H:%M:%S')}] Sending request to {url}")
    print(f"Query: '{query}'\n")
    
    start_time = time.time()
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                
                print(f"Status Code: {response.status}")
                if response.status != 200:
                    text = await response.text()
                    print(f"Error Response: {text}")
                    return
                
                data = await response.json()
                elapsed = time.time() - start_time
                
                print(f"Response Time: {elapsed:.2f} seconds")
                print("\n--- Response Content ---")
                
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                print(content)
                
                print("\n--- Full JSON Payload ---")
                pprint(data)

    except Exception as e:
        print(f"Test failed with error: {e}")

async def test_live_stream(base_url, query, token):
    url = f"{base_url}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": query}],
        "stream": True
    }
    
    print(f"\n[{time.strftime('%H:%M:%S')}] Sending STREAMING request to {url}")
    print(f"Query: '{query}'\n")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                print(f"Status Code: {response.status}\n")
                if response.status != 200:
                    print(f"Error Response: {await response.text()}")
                    return
                
                print("--- Stream Started ---")
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if not line: continue
                    
                    if line.startswith("data: "):
                        data_str = line.replace("data: ", "")
                        if "[DONE]" in data_str:
                            break
                        try:
                            d = json.loads(data_str)
                            delta = d.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                print(content, end="", flush=True)
                        except json.JSONDecodeError:
                            pass
                print("\n\n--- Stream Ended ---")
    except Exception as e:
        print(f"Stream failed with error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Live SharedLLM API")
    parser.add_argument("--host", default="http://127.0.0.1:11435", help="Base URL of the API (default: http://127.0.0.1:11435)")
    parser.add_argument("--query", default="What is the state of the living room tv?", help="Query to send")
    parser.add_argument("--token", default="test_token", help="Bearer token for auth")
    parser.add_argument("--stream", action="store_true", help="Test the streaming endpoint")
    
    args = parser.parse_args()
    
    if args.stream:
        asyncio.run(test_live_stream(args.host, args.query, args.token))
    else:
        asyncio.run(test_live_chat(args.host, args.query, args.token))
