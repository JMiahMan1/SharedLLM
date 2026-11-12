"""The reverse proxy's site for the companion apps: a configured host, only the app's paths, secrets kept out of logs."""
from __future__ import annotations

import os
import re

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _site() -> str:
    with open(os.path.join(ROOT, "Caddyfile"), encoding="utf-8") as f:
        text = f.read()
    start = text.index("http://{$HOMEHUB_EXTERNAL_HOST")
    # The site's own brace comes after the host placeholder's closing one.
    opening = text.index("{", text.index("}", start) + 1)
    depth = 0
    for end in range(opening, len(text)):
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        if depth == 0:
            return text[start:end + 1]
    raise AssertionError("the companion site block is not closed")


def test_the_host_comes_from_the_environment_not_the_file():
    site = _site()
    assert site.startswith("http://{$HOMEHUB_EXTERNAL_HOST:homehub.invalid} {")
    assert "reverse_proxy {$HOMEHUB_UPSTREAM:host.docker.internal:8123}" in site
    # No real domain may be written into the repository's proxy config for the hub.
    hosts = re.findall(r"[a-z0-9-]+\.[a-z]{2,}(?:\.[a-z]{2,})?", site)
    assert set(hosts) <= {"homehub.invalid", "host.docker.internal"}


def test_only_the_companion_apps_paths_reach_the_hub():
    site = _site()
    matcher = re.search(r"@companion path (.+)", site).group(1).split()
    assert matcher == ["/api/webhook/*", "/api/mobile_app/registrations", "/auth/*", "/api/websocket"]
    assert site.count("reverse_proxy") == 1  # nothing else is proxied
    assert re.search(r"handle \{\s*respond 404\s*\}", site)
    for blocked in ("/hub", "/api/states", "/api/services", "/api/camera_proxy", "/api/template"):
        assert blocked not in site


def test_webhook_ids_and_tokens_stay_out_of_the_access_log():
    site = _site()
    assert "request>uri regexp ^/api/webhook/[^/?]+ /api/webhook/REDACTED" in site
    assert "request>headers>Authorization delete" in site


def test_compose_gives_caddy_the_host_name_and_a_way_to_the_hub():
    with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as f:
        caddy = yaml.safe_load(f)["services"]["caddy"]
    env = "\n".join(caddy["environment"])
    assert "HOMEHUB_EXTERNAL_HOST=${HOMEHUB_EXTERNAL_HOST:-homehub.invalid}" in env
    assert "HOMEHUB_UPSTREAM=${HOMEHUB_UPSTREAM:-host.docker.internal:8123}" in env
    assert "host.docker.internal:host-gateway" in caddy["extra_hosts"]
