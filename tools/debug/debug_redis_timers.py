import json
import os
import sys
from datetime import datetime
from typing import Any

import redis
from dotenv import load_dotenv

# Load Env
load_dotenv()
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

print(f"\n{'='*50}")
print("DIAGNOSTIC: REDIS TIMER STORAGE")
print(f"{'='*50}")
print(f"Connecting to: {REDIS_URL}")

try:
    r: redis.Redis[Any] = redis.from_url(REDIS_URL, decode_responses=True)  # type: ignore[assignment]
    r.ping()
    print("[OK] Redis Connection Successful.")
except Exception as e:
    print(f"[FAIL] Redis Connection Error: {e}")
    sys.exit(1)

# 1. Dump All Timer Keys
keys: list[str] = r.keys("rag:timers:*")  # type: ignore[assignment]
print(f"\nFound {len(keys)} timer keys in Redis:")

if not keys:
    print("   [WARNING] No timers found in database. Persistence failed during creation.")
else:
    now = datetime.now()
    print(f"Current System Time: {now} (iso: {now.isoformat()})")

    for k in keys:
        val = r.get(k)  # type: ignore[misc]
        print(f"\n--- Key: {k} ---")
        print(f"Raw Value: {val}")

        try:
            data = json.loads(val) if val is not None else {}  # type: ignore[arg-type]
            expires_at_str = data.get("expires_at")

            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str)
                remaining = (expires_at - now).total_seconds()

                print(f"   Parsed Expiry: {expires_at}")
                print(f"   Remaining Seconds: {remaining}")

                if remaining < 0:
                    print("   [STATUS] EXPIRED (List function will hide this)")
                else:
                    print("   [STATUS] ACTIVE (List function should show this)")
            else:
                print("   [ERROR] Missing 'expires_at' field")

        except Exception as e:
            print(f"   [ERROR] JSON Decode/Parse Error: {e}")

print(f"\n{'='*50}\n")
