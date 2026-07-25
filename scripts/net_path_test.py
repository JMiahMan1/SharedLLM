#!/usr/bin/env python3
"""Network path test for the SharedLLM server (192.168.2.205:11435).

Hypothesis under test: the intermittent HTTP 000 / latency spikes to the gateway
are caused by the server's 2.4 GHz Wi-Fi uplink (router AP wl0-ap0, ch 6),
NOT by the gateway/RAG processes (which are healthy) and NOT by packet loss
on the wired portion of the path.

This script measures, over a sustained window:
  - TCP connect+HTTP latency to the gateway /health (trivial, should be ~ms)
  - connection failure rate (HTTP 000 / timeout)
  - latency under concurrent load (simulates the deploy / parallel-probe conditions
    that previously triggered HTTP 000)

Run: python3 net_path_test.py [--duration 60] [--concurrency 5] [--url ...]
"""
import argparse
import asyncio
import statistics
import time
from collections import Counter

try:
    import aiohttp
except ImportError:
    aiohttp = None

URL_HEALTH = "http://192.168.2.205:11435/health"
URL_RAG = "http://192.168.2.205:11435/api/storage/learning?user_id=default&limit=5"


async def probe(session, url, timeout):
    t0 = time.monotonic()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            await resp.read()
            dt = (time.monotonic() - t0) * 1000.0
            return resp.status, dt
    except TimeoutError:
        return ("TIMEOUT", (time.monotonic() - t0) * 1000.0)
    except Exception as e:  # connection reset / refused / 000
        return (f"ERR:{type(e).__name__}", (time.monotonic() - t0) * 1000.0)


async def run(duration, concurrency, url, timeout):
    if aiohttp is None:
        raise SystemExit("aiohttp not installed; pip install aiohttp")
    connector = aiohttp.TCPConnector(limit=concurrency, limit_per_host=concurrency)
    results = []
    sem = asyncio.Semaphore(concurrency)

    async def worker():
        end = time.monotonic() + duration
        while time.monotonic() < end:
            async with sem:
                results.append(await probe(session, url, timeout))
            await asyncio.sleep(0.2)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await asyncio.gather(*tasks)

    statuses = Counter(s for s, _ in results)
    lat = [dt for s, dt in results if isinstance(s, int) and s == 200]
    print("\n=== NET PATH TEST ===")
    print(f"target          : {url}")
    print(f"duration        : {duration}s  concurrency: {concurrency}")
    print(f"total probes    : {len(results)}")
    print(f"status counts   : {dict(statuses)}")
    if lat:
        print(f"latency ms     : min={min(lat):.1f} median={statistics.median(lat):.1f} "
              f"mean={statistics.mean(lat):.1f} p95={sorted(lat)[int(len(lat)*0.95)]:.1f} "
              f"max={max(lat):.1f}")
        fails = len(results) - len(lat)
        print(f"failure rate   : {100.0*fails/len(results):.1f}% "
              f"({fails} non-200/timeout/err of {len(results)})")
        # Jitter: stdev of successful latencies.
        print(f"jitter (stdev): {statistics.pstdev(lat):.1f} ms")
    else:
        print("NO SUCCESSFUL PROBES — path fully down during window")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=45)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--url", default=URL_HEALTH)
    args = ap.parse_args()
    asyncio.run(run(args.duration, args.concurrency, args.url, args.timeout))


if __name__ == "__main__":
    main()
