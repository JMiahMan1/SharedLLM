"""Which image a container runs, and whether its registry has a newer one.

Kept apart from main.py so it can be tested without Docker.
"""
from __future__ import annotations

import logging
import os
import re

import requests

log = logging.getLogger("control_plane")

# Every manifest form a registry may answer with; a multi-arch index first.
MANIFEST_ACCEPT = ",".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))

_KNOWN_SERVICES = {
    "gateway", "identity", "rag", "storage", "logging", "workspace_runtime",
    "control_plane", "geo", "ui", "caddy", "bible", "dns", "telemetry",
    "automation", "execution",
}


class RegistryUnreachable(Exception):
    """The registry could not be reached at all (DNS, TLS, timeout)."""


def infer_ghcr_ref(service_name: str) -> str | None:
    """ghcr.io/{GHCR_NAMESPACE}/sharedllm-{service}:latest for a known service."""
    name = service_name
    if name.startswith("sharedllm_"):
        name = name[len("sharedllm_"):]
    if name.endswith("_1"):
        name = name[:-2]
    if name not in _KNOWN_SERVICES:
        return None
    namespace = os.getenv("GHCR_NAMESPACE", "jmiahman1")
    return f"ghcr.io/{namespace}/sharedllm-{name}:latest"


def container_image_ref(container, service_name: str = "") -> str | None:
    """The image reference the container was created from (e.g. ``...:latest``).

    ``Config.Image`` comes first: ``image.tags[0]`` can be a commit tag
    (``...:61caa665``) that never moves, so pulling or checking it never
    finds an update.
    """
    attrs = getattr(container, "attrs", None) or {}
    configured = (attrs.get("Config") or {}).get("Image") or ""
    if configured and not configured.startswith("sha256:"):
        return configured
    labels = getattr(container, "labels", None) or {}
    if labels.get("com.docker.compose.image"):
        return labels["com.docker.compose.image"]
    image = getattr(container, "image", None)
    tags = list(getattr(image, "tags", None) or [])
    latest = [t for t in tags if t.endswith(":latest")]
    if latest or tags:
        return (latest or tags)[0]
    return infer_ghcr_ref(service_name or getattr(container, "name", ""))


def parse_ref(ref: str) -> tuple[str, str, str]:
    """(registry, repository, tag) for an image reference."""
    name, tag = ref, "latest"
    last = ref.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = ref.rsplit(":", 1)
    first = name.split("/", 1)[0]
    if "/" in name and ("." in first or ":" in first or first == "localhost"):
        registry, repo = name.split("/", 1)
    else:
        registry = "registry-1.docker.io"
        repo = name if "/" in name else f"library/{name}"
    return registry, repo, tag


def local_digests(image) -> set[str]:
    """Every sha256 the image is known by in its registry (one per pulled tag)."""
    attrs = getattr(image, "attrs", None) or {}
    return {d.split("@", 1)[1] for d in attrs.get("RepoDigests") or [] if "@" in d}


def _bearer(challenge: str, token: str | None, timeout: float) -> str | None:
    """Exchange a ``WWW-Authenticate: Bearer`` challenge for a pull token.

    Uses the GitHub token when there is one (private images); public images
    need none, so a rejected token falls back to an anonymous pull token.
    """
    params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    realm = params.pop("realm", None)
    if not realm:
        return None
    attempts = [("token", token), None] if token else [None]
    for auth in attempts:
        try:
            resp = requests.get(realm, params=params, auth=auth, timeout=timeout)
        except requests.RequestException as e:
            raise RegistryUnreachable(str(e)) from e
        if resp.status_code == 200:
            body = resp.json()
            return body.get("token") or body.get("access_token")
    return None


def remote_digest(ref: str, token: str | None = None, timeout: float = 10.0) -> str | None:
    """The registry's current digest for ``ref``, without pulling.

    Returns None when the registry answers but will not say (unknown tag, no
    access). Raises RegistryUnreachable when it cannot be reached.
    """
    registry, repo, tag = parse_ref(ref)
    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    headers = {"Accept": MANIFEST_ACCEPT}
    try:
        resp = requests.head(url, headers=headers, timeout=timeout)
        if resp.status_code == 401:
            bearer = _bearer(resp.headers.get("WWW-Authenticate", ""), token, timeout)
            if not bearer:
                log.warning(f"[updates] {registry} gave no pull token for {repo}")
                return None
            resp = requests.head(url, headers={**headers, "Authorization": f"Bearer {bearer}"}, timeout=timeout)
    except requests.RequestException as e:
        raise RegistryUnreachable(str(e)) from e
    if resp.status_code != 200:
        log.warning(f"[updates] HTTP {resp.status_code} for {repo}:{tag}")
        return None
    return resp.headers.get("Docker-Content-Digest")
