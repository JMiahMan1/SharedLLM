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
    # The app's own paths, plus the pictures and audio a notification links to: each of those is guarded by
    # the hub itself (a rotating token, or a bearer token and may_view; a TTS clip's unguessable name).
    assert matcher == ["/api/webhook/*", "/api/mobile_app/registrations", "/auth/*", "/api/websocket",
                       "/api/camera_proxy/*", "/api/camera_proxy_stream/*", "/api/image_proxy/*",
                       "/api/tts_proxy/*"]
    assert site.count("reverse_proxy") == 1  # nothing else is proxied
    assert re.search(r"handle \{\s*respond 404\s*\}", site)
    code = "\n".join(line for line in site.splitlines() if not line.strip().startswith("#"))
    for blocked in ("/hub", "/api/states", "/api/services", "/api/template", "/api/config", "/api/history"):
        assert blocked not in code


def _log_redaction():
    """The access log's URI filter, as Python: the pattern, and Go's ``${1}`` as ``\\g<1>``."""
    m = re.search(r"request>uri regexp (\S+) (\S+)", _site())
    return re.compile(m.group(1)), m.group(2).replace("${1}", r"\g<1>")


def test_webhook_ids_and_tokens_stay_out_of_the_access_log():
    site = _site()
    pattern, repl = _log_redaction()
    secret = "ab" * 32
    assert pattern.sub(repl, f"/api/webhook/{secret}") == "/api/webhook/REDACTED"
    assert pattern.sub(repl, f"/api/tts_proxy/{secret}.wav") == "/api/tts_proxy/REDACTED"
    assert pattern.sub(repl, f"/api/camera_proxy/camera.door?token={secret}&width=640") == \
        "/api/camera_proxy/camera.door?token=REDACTED&width=640"
    assert pattern.sub(repl, "/api/websocket") == "/api/websocket"
    for header in ("Authorization", "X-Internal-Secret", "X-Api-Key", "X-Webhook-Secret", "Cookie"):
        assert f"request>headers>{header} delete" in site


def test_compose_gives_caddy_the_host_name_and_a_way_to_the_hub():
    with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as f:
        caddy = yaml.safe_load(f)["services"]["caddy"]
    env = "\n".join(caddy["environment"])
    assert "HOMEHUB_EXTERNAL_HOST=${HOMEHUB_EXTERNAL_HOST:-homehub.invalid}" in env
    assert "HOMEHUB_UPSTREAM=${HOMEHUB_UPSTREAM:-host.docker.internal:8123}" in env
    assert "host.docker.internal:host-gateway" in caddy["extra_hosts"]
