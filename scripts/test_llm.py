import asyncio
import httpx

async def main():
    prompt = "The user asked 'Execute the StorageIndexRequest tool for the path /Notes' but lacks ['nextcloud_url', 'nextcloud_user', 'nextcloud_pass']. Explain that they must visit the Identity page."
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://127.0.0.1:11434/api/generate",
            json={"model": "qwen3:latest", "prompt": prompt, "stream": False},
            timeout=60.0
        )
        print(resp.json().get("response"))

asyncio.run(main())
