"""ntfy limits for outside UnifiedPush senders (docs/PUSH.md, "Limits").

Outside servers may POST to /push/up* through Caddy (everyone:up*:wo). These
checks keep that path bounded: real client addresses reach ntfy, only the
gateway and telemetry are exempt from rate limits, and messages are capped
in number, size and age.
"""
import os
import re

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(name: str) -> str:
    with open(os.path.join(ROOT, name)) as f:
        return f.read()


def _ntfy() -> dict:
    return yaml.safe_load(_read("ntfy/server.yml"))


def _services() -> dict:
    return yaml.safe_load(_read("docker-compose.yml"))["services"]


def test_only_the_gateway_and_telemetry_are_exempt():
    exempt = {h.strip() for h in _ntfy()["visitor-request-limit-exempt-hosts"].split(",")}
    services = _services()
    fixed = {services[name]["networks"]["sharedllm"]["ipv4_address"] for name in ("gateway", "telemetry")}
    assert exempt == fixed
    assert not any("/" in h for h in exempt), "a whole network must not be exempt"


def test_outside_senders_are_capped():
    conf = _ntfy()
    assert conf["behind-proxy"] is True
    assert conf["message-size-limit"] == 4096
    assert 0 < conf["visitor-message-daily-limit"] <= 5000
    assert conf["cache-duration"] == "12h"
    assert "everyone:up*:wo" in conf["auth-access"]
    # Subscriber-based limiting would charge outside senders to the gateway,
    # which is exempt: it must stay off.
    assert not conf.get("visitor-subscriber-rate-limiting")


def test_the_public_proxy_is_trusted_on_both_sides():
    services = _services()
    assert "PUBLIC_PROXY_IP=${PUBLIC_PROXY_IP:-127.0.0.1}" in services["caddy"]["environment"]
    assert "NTFY_PROXY_TRUSTED_HOSTS=${PUBLIC_PROXY_IP:-127.0.0.1}" in services["ntfy"]["environment"]
    caddyfile = _read("Caddyfile")
    assert re.search(r"servers :80 \{\s*trusted_proxies static \{\$PUBLIC_PROXY_IP\}\s*\}", caddyfile)


def test_caddy_refuses_bodies_over_4096_bytes_on_the_push_path():
    caddyfile = _read("Caddyfile")
    block = caddyfile[caddyfile.index("handle /push/up* {"):]
    block = block[:block.index("respond 405")]
    assert re.search(r"request_body \{\s*max_size 4096\s*\}", block)
    assert block.index("request_body") < block.index("reverse_proxy ntfy:80")
