#!/usr/bin/env python3
import logging
import os
import sys

import redis

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger("system_health")

def check_redis():
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    log.info(f"Checking Redis connection to {redis_url}...")
    try:
        r = redis.Redis.from_url(redis_url, socket_timeout=2)
        r.ping()
        log.info("  [OK] Redis is reachable.")
        return True
    except Exception as e:
        log.error(f"  [FAIL] Redis unreachable: {e}")
        return False

def check_ha_vars():
    log.info("Checking Home Assistant Configuration...")
    url = os.getenv("HA_URL")
    token = os.getenv("HA_TOKEN")

    if url:
        log.info(f"  [OK] HA_URL is set: {url}")
    else:
        log.error("  [FAIL] HA_URL is missing.")

    if token:
         log.info("  [OK] HA_TOKEN is set.")
    else:
         log.error("  [FAIL] HA_TOKEN is missing.")

    return bool(url and token)

def check_chroma_path():
    path = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
    log.info(f"Checking ChromaDB Path: {path}")
    if os.path.exists(path):
         log.info("  [OK] Directory exists.")
         # Try writing a test file if writable check needed?
         # Assuming existence suffices for now.
    else:
         log.warning(f"  [WARN] Directory {path} does not exist (might be created on app start).")

def main():
    print("=== System Health Diagnostic ===")

    redis_ok = check_redis()
    ha_ok = check_ha_vars()
    check_chroma_path()

    if redis_ok and ha_ok:
        print("\nAll core infrastructure checks PASSED.")
        sys.exit(0)
    else:
        print("\nSome infrastructure checks FAILED.")
        sys.exit(1)

if __name__ == "__main__":
    main()
