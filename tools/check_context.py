
import redis
import os
import sys

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

def check_context():
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        user = "admin"
        key = f"rag:last_media_entity:{user}"
        val = r.get(key)
        print(f"Key: {key}")
        print(f"Value: {val}")
        
        # Check explicit device resolution for "Office TV"
        # We can simulate what smart_resolve_entity does if we had the library, 
        # but here we just check the output state.
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_context()
