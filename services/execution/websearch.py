"""Web search utility using SearXNG."""
import logging
import urllib.parse
from typing import Any

from services.common.http import get_client

log = logging.getLogger("execution.websearch")


async def web_search(query: str, num_results: int = 5) -> list[dict[str, Any]]:
    """Search via SearXNG JSON API, with fallback to empty results."""
    try:
        import aiohttp
        searxng_url = None
        try:
            from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET
            async with get_client() as client:
                resp = await client.get(
                    f"{IDENTITY_SVC_URL}/api/settings",
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=5.0),
                )
                if resp.status == 200:
                    for item in await resp.json():
                        if item.get("key") == "searxng_url":
                            url = item.get("value", "").rstrip("/")
                            if url:
                                searxng_url = url
                                break
        except Exception:
            pass

        if not searxng_url:
            searxng_url = "http://localhost:8080"

        params = {
            "q": query,
            "format": "json",
            "pageno": 1,
            "categories": "general",
        }
        url = f"{searxng_url}/search?{urllib.parse.urlencode(params)}"
        async with get_client() as client:
            resp = await client.get(url, timeout=aiohttp.ClientTimeout(total=10.0))
            if resp.status == 200:
                data = await resp.json()
                results = []
                for r in data.get("results", [])[:num_results]:
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("content", ""),
                        "url": r.get("url", ""),
                    })
                return results
    except Exception as e:
        log.warning(f"[websearch] Search failed: {e}")

    return []
