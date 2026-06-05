#!/usr/bin/env python3
"""Debug Audiobookshelf login flow — test with stored credentials."""
import asyncio
import httpx
import os
import sys
from pathlib import Path

# Load .env from project root
env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


async def test():
    abs_url = os.getenv("AUDIOBOOKSHELF_URL") or os.getenv("ABS_URL")
    username = os.getenv("AUDIOBOOKSHELF_USER") or os.getenv("ABS_USER")
    password = os.getenv("AUDIOBOOKSHELF_PASS") or os.getenv("ABS_PASS")

    print(f"ABS URL: {abs_url}")
    print(f"Username: {username}")
    print(f"Password: {'*' * len(password) if password else '(empty)'}")
    print()

    if not all([abs_url, username, password]):
        print("ERROR: Missing credentials in environment")
        sys.exit(1)

    # Ensure URL doesn't have trailing slash for API calls
    base = abs_url.rstrip("/")
    login_url = f"{base}/api/login"

    print(f"POST {login_url}")
    print(f"Body: {{'username': '{username}', 'password': '***'}}")
    print()

    async with httpx.AsyncClient(timeout=15, verify=True) as client:
        try:
            resp = await client.post(
                login_url,
                json={"username": username, "password": password},
            )
            print(f"Status: {resp.status_code}")
            print(f"Headers: {dict(resp.headers)}")
            print(f"Body: {resp.text[:2000]}")

            if resp.status_code == 200:
                data = resp.json()
                token = data.get("user", {}).get("token")
                if token:
                    print(f"\nSUCCESS: Got token: {token[:30]}...")
                else:
                    print(f"\nWARN: No token in response. Keys: {list(data.keys())}")
            elif resp.status_code == 401:
                print(f"\nERROR: 401 Unauthorized — credentials rejected by server")
                # Try with different formats
                print("\n--- Trying alternative formats ---\n")
                
                # Try email format
                email = f"{username}@sumemail.com"
                email_url = f"{base}/api/login"
                print(f"POST {email_url} with username={email}")
                resp2 = await client.post(
                    email_url,
                    json={"username": email, "password": password},
                )
                print(f"Status: {resp2.status_code}")
                print(f"Body: {resp2.text[:500]}")
                
                # Try with email @ sumemail.com
                print(f"\nPOST {email_url} with username={username}@abs.sumemail.com")
                resp3 = await client.post(
                    email_url,
                    json={"username": f"{username}@abs.sumemail.com", "password": password},
                )
                print(f"Status: {resp3.status_code}")
                print(f"Body: {resp3.text[:500]}")

        except httpx.ConnectError as e:
            print(f"CONNECTION ERROR: {e}")
            print(f"Can't reach {abs_url}")
        except httpx.TimeoutException as e:
            print(f"TIMEOUT: {e}")
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(test())
