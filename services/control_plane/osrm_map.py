"""Extend the road map (OSRM) to where trips go, and deploy it when rebuilt.

geo records the Geofabrik regions trips were in that the map lacks
(GET /map/regions, services/geo/map_regions.py). Hourly, this:

1. appends any of them missing from osrm/regions.txt through the GitHub API,
   as a commit -- the push runs the "Build OSRM map" workflow, which prepares
   the map on a GitHub runner (the server lacks the memory) and publishes
   ghcr.io/.../sharedllm-osrm; the repo file stays the one record of the map;
2. pulls and recreates the osrm container once a map build has succeeded
   after the container was created.

Needs GITHUB_REPOSITORY (owner/name) and OSRM_MAP_BRANCH in the environment
and a GitHub token with repo + workflow scopes; without them it says so in the
log and does nothing.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime

import requests

log = logging.getLogger("control_plane.osrm_map")

REGIONS_PATH = "osrm/regions.txt"
WORKFLOW_FILE = "osrm-map.yml"
GITHUB_API = "https://api.github.com"


def _config() -> tuple[str, str] | None:
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    branch = os.getenv("OSRM_MAP_BRANCH", "").strip()
    if not repo or not branch:
        return None
    return repo, branch


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def regions_file(repo: str, branch: str, token: str) -> tuple[str, str]:
    """(text, blob sha) of osrm/regions.txt on the branch."""
    resp = requests.get(f"{GITHUB_API}/repos/{repo}/contents/{REGIONS_PATH}", params={"ref": branch},
                        headers=_headers(token), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]


def missing_regions(text: str, needed: list[dict]) -> list[dict]:
    """The needed regions whose download URL is not in the file yet."""
    present = {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}
    seen: set[str] = set()
    out = []
    for region in needed:
        url = (region.get("url") or "").strip()
        if url.startswith("https://download.geofabrik.de/") and url.endswith(".osm.pbf") \
                and url not in present and url not in seen:
            seen.add(url)
            out.append(region)
    return out


def extend(needed: list[dict], token: str) -> list[str]:
    """Commit any missing regions to osrm/regions.txt; returns their names."""
    cfg = _config()
    if not cfg:
        log.info("[osrm-map] GITHUB_REPOSITORY / OSRM_MAP_BRANCH not set; not extending the map")
        return []
    repo, branch = cfg
    text, sha = regions_file(repo, branch, token)
    add = missing_regions(text, needed)
    if not add:
        return []
    new_text = text.rstrip("\n") + "\n" + "\n".join(r["url"] for r in add) + "\n"
    names = [r.get("name") or r["url"] for r in add]
    resp = requests.put(
        f"{GITHUB_API}/repos/{repo}/contents/{REGIONS_PATH}",
        headers=_headers(token), timeout=15,
        data=json.dumps({
            "message": f"Map: add {', '.join(names)} (trips went there)",
            "content": base64.b64encode(new_text.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": branch,
        }),
    )
    resp.raise_for_status()
    log.info(f"[osrm-map] Added {names} to {REGIONS_PATH}; the map rebuilds on GitHub")
    return names


def latest_build_finished_at(token: str) -> datetime | None:
    """When the newest successful map build finished, or None."""
    cfg = _config()
    if not cfg:
        return None
    repo, branch = cfg
    resp = requests.get(f"{GITHUB_API}/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs",
                        params={"branch": branch, "status": "success", "per_page": 1},
                        headers=_headers(token), timeout=15)
    resp.raise_for_status()
    runs = resp.json().get("workflow_runs") or []
    if not runs:
        return None
    return datetime.fromisoformat(runs[0]["updated_at"].replace("Z", "+00:00"))


def needs_deploy(container_created: str | None, built_at: datetime | None) -> bool:
    """True when a map build finished after the running container was created."""
    if not built_at:
        return False
    if not container_created:
        return True
    created = datetime.fromisoformat(container_created.split(".")[0].replace("Z", "") + "+00:00")
    return built_at > created
