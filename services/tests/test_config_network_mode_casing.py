"""services/config.py must read the network-mode env vars with the case .env uses.

Bug found while chasing a garbled ``SEARXNG_URL`` in the execution container.
Two independent defects stacked:

1. ``NETWORK_MODE`` is lowercased at import (``"host"``), and two lookups
   interpolated it *without* re-uppercasing::

       SEARXNG_URL = os.getenv(f"{NETWORK_MODE}_SEARXNG_URL")   # -> "host_SEARXNG_URL"

   but ``.env`` spells it ``HOST_SEARXNG_URL``, so the lookup always missed and
   silently fell through to the un-namespaced ``SEARXNG_URL``. The sibling helper
   ``_net_url`` has always used ``.upper()``, so the two disagreed.

2. docker-compose.yml folded a stray-indented ``- EXECUTION_EXTERNAL_HOST=...``
   into the preceding ``SEARXNG_URL=`` line, so that fallback was itself a
   garbage literal.

Fixing only one leaves it broken, which is why both are guarded here.

The Identity *setting keys* (``host_searxng_url``) are lowercase by convention
and must stay that way — see the note in ``settings_map``.
"""
import importlib
import json
import os
import subprocess

import pytest


def _load_config(env: dict[str, str]):
    """Import services.config fresh with a controlled environment."""
    saved = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        for mod in [m for m in list(importlib.sys.modules) if m == "services.config"]:
            del importlib.sys.modules[mod]
        return importlib.import_module("services.config")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


HOST_ENV = {
    "NETWORK_MODE": "host",
    "HOST_SEARXNG_URL": "http://host-search:8080",
    "BRIDGE_SEARXNG_URL": "http://bridge-search:8080",
    "SEARXNG_URL": "",
    "HOST_REDIS_URL": "redis://host-redis:6379/0",
    "BRIDGE_REDIS_URL": "redis://bridge-redis:6379/0",
    "REDIS_URL": "",
}

BRIDGE_ENV = {
    "NETWORK_MODE": "bridge",
    "HOST_SEARXNG_URL": "http://host-search:8080",
    "BRIDGE_SEARXNG_URL": "http://bridge-search:8080",
    "SEARXNG_URL": "",
    "HOST_REDIS_URL": "redis://host-redis:6379/0",
    "BRIDGE_REDIS_URL": "redis://bridge-redis:6379/0",
    "REDIS_URL": "",
}


class TestSearxngIsNetworkAware:
    def test_host_mode_reads_the_uppercase_HOST_variable(self):
        cfg = _load_config(HOST_ENV)
        assert cfg.SEARXNG_URL == "http://host-search:8080"

    def test_bridge_mode_reads_BRIDGE(self):
        cfg = _load_config(BRIDGE_ENV)
        assert cfg.SEARXNG_URL == "http://bridge-search:8080"

    def test_network_mode_is_stored_lowercase(self):
        """The lowercase storage is fine; the lookup is what must uppercase."""
        cfg = _load_config(HOST_ENV)
        assert cfg.NETWORK_MODE == "host"

    def test_falls_back_to_the_unprefixed_variable(self):
        env = dict(HOST_ENV)
        env["HOST_SEARXNG_URL"] = ""
        env["SEARXNG_URL"] = "http://legacy:8080"
        cfg = _load_config(env)
        assert cfg.SEARXNG_URL == "http://legacy:8080"

    def test_does_not_pick_the_other_network_modes_value(self):
        """A bridge value must never satisfy a host-networked service."""
        env = dict(HOST_ENV)
        env["HOST_SEARXNG_URL"] = ""
        cfg = _load_config(env)
        assert cfg.SEARXNG_URL != "http://bridge-search:8080"

    def test_empty_when_nothing_is_configured(self):
        env = dict(HOST_ENV)
        env["HOST_SEARXNG_URL"] = ""
        cfg = _load_config(env)
        # Not the default: no silent fallback to a guessed host.
        assert cfg.SEARXNG_URL == ""


class TestRedisIsNetworkAware:
    def test_host_mode_reads_the_uppercase_HOST_variable(self):
        cfg = _load_config(HOST_ENV)
        assert cfg.REDIS_URL == "redis://host-redis:6379/0"

    def test_bridge_mode_reads_BRIDGE(self):
        cfg = _load_config(BRIDGE_ENV)
        assert cfg.REDIS_URL == "redis://bridge-redis:6379/0"

    def test_falls_back_to_the_unprefixed_variable(self):
        env = dict(HOST_ENV)
        env["HOST_REDIS_URL"] = ""
        env["REDIS_URL"] = "redis://legacy:6379/0"
        cfg = _load_config(env)
        assert cfg.REDIS_URL == "redis://legacy:6379/0"


class TestMatchesTheNetUrlHelper:
    """`_net_url` and the inline lookups must agree on casing.

    `_net_url` has always uppercased; when the inline ones did not, a service
    could resolve one set of addresses through `_net_url` and a different (or
    missing) one for SEARXNG/Redis in the same process.
    """

    def test_searxng_matches_net_url_casing(self):
        env = dict(HOST_ENV)
        env["HOST_IDENTITY_SVC_URL"] = "http://127.0.0.1:8001"
        cfg = _load_config(env)
        # `_net_url` and SEARXNG must both honour HOST_* in host mode. When only
        # one did, a process resolved identity via one convention and SearXNG via
        # another — and SearXNG silently fell through to an unrelated value.
        assert cfg.IDENTITY_SVC_URL == "http://127.0.0.1:8001"
        assert cfg.SEARXNG_URL == "http://host-search:8080"

    def test_net_url_also_reads_host_prefix_in_host_mode(self):
        env = dict(HOST_ENV)
        env["HOST_EXECUTION_SVC_URL"] = "http://127.0.0.1:8003"
        cfg = _load_config(env)
        assert cfg.EXECUTION_SVC_URL == "http://127.0.0.1:8003"


class TestIdentitySettingKeysStayLowercase:
    """The settings_map keys are Identity setting keys, not env vars."""

    def test_settings_map_uses_lowercase_prefix(self):
        """The keys live inside resolve_runtime_config(), not at module scope."""
        import inspect

        from services import config as cfg_mod

        src = inspect.getsource(cfg_mod.resolve_runtime_config)
        assert 'f"{NETWORK_MODE}_searxng_url"' in src
        assert 'f"{NETWORK_MODE}_execution_svc_url"' in src
        # An uppercase variant would never match a stored setting, and these are
        # Identity setting keys — .env's uppercase HOST_* spelling is a
        # different namespace entirely.
        assert 'f"{NETWORK_MODE.upper()}_searxng_url"' not in src


class TestComposeHasNoFoldedOrHardcodedHost:
    """docker-compose.yml must not fold env entries or bake in a LAN address."""

    @staticmethod
    def _compose() -> dict:
        import yaml

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "docker-compose.yml")) as f:
            return yaml.safe_load(f)

    def test_every_execution_env_entry_is_a_single_variable(self):
        """A stray space folds two entries into one garbled value.

        The fold we hit looked like::

            - SEARXNG_URL=${HOST_SEARXNG_URL}
             - EXECUTION_EXTERNAL_HOST=192.168.2.205

        which YAML reads as ONE value, producing
        ``SEARXNG_URL=http://... - EXECUTION_EXTERNAL_HOST=192.168.2.205`` and
        never setting EXECUTION_EXTERNAL_HOST at all.
        """
        import re

        for entry in self._compose()["services"]["execution"]["environment"]:
            assert isinstance(entry, str)
            assert "\n" not in entry
            name, _, value = entry.partition("=")
            # No baked-in private address anywhere in the value.
            assert not re.search(r"\b(?:192\.168|10)\.\d+\.\d+\.\d+\b", value), (
                f"{name} hardcodes a private address: {value!r}"
            )
            # A value that interpolates must be exactly one ${VAR} reference.
            # This is what catches the fold: a merged entry's value is
            # "http://host:8080 - EXECUTION_EXTERNAL_HOST=1.2.3.4", which matches
            # neither this pattern nor the no-private-address rule above.
            if "${" in value:
                # ${VAR}, ${VAR:?required}, ${VAR:-default} — and nothing else.
                assert re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*(:[?-][^}]*)?\}", value), (
                    f"{name} has extra text around the reference: {value!r}"
                )

    def test_no_private_lan_addresses_are_baked_in(self):
        import re

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "docker-compose.yml")) as f:
            text = f.read()
        # 192.168.x.y / 10.x — the RFC1918 ranges that would break other installs.
        assert not re.search(r"\b(?:192\.168|10)\.\d+\.\d+\.\d+\b", text), (
            "docker-compose.yml hardcodes a private address"
        )

    def test_execution_external_host_comes_from_env(self):
        entries = self._compose()["services"]["execution"]["environment"]
        hits = [e for e in entries if e.startswith("EXECUTION_EXTERNAL_HOST=")]
        assert len(hits) == 1
        assert hits[0] == "EXECUTION_EXTERNAL_HOST=${EXECUTION_EXTERNAL_HOST}"

    def test_searxng_is_not_corrupted(self):
        entries = self._compose()["services"]["execution"]["environment"]
        searx = [e for e in entries if e.startswith("SEARXNG_URL=")]
        assert len(searx) == 1
        assert searx[0] == "SEARXNG_URL=${HOST_SEARXNG_URL}"

    def test_execution_still_uses_the_host_network(self):
        """The cross-network design depends on this; see
        docs/CADDY_CROSS_NETWORK_IMPLEMENTATION.md."""
        assert self._compose()["services"]["execution"]["network_mode"] == "host"

    def test_dns_service_is_untouched(self):
        dns = self._compose()["services"]["dns"]
        assert dns["networks"]["sharedllm"]["ipv4_address"] == "172.26.0.254"
        assert dns["ports"] == ["15353:53/udp", "15353:53/tcp"]


class TestComposeConfigResolves:
    """What compose actually interpolates must be clean."""

    @staticmethod
    def _rendered() -> dict:
        import yaml

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=root, capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            pytest.skip("docker compose unavailable in this environment")
        return json.loads(out.stdout)

    def test_searxng_resolves_to_a_bare_url(self):
        env = self._rendered()["services"]["execution"]["environment"]
        assert env["SEARXNG_URL"] == env["SEARXNG_URL"].strip()
        assert "EXECUTION_EXTERNAL_HOST" not in env["SEARXNG_URL"]
        assert " - " not in env["SEARXNG_URL"]

    def test_external_host_matches_the_env_file(self):
        env = self._rendered()["services"]["execution"]["environment"]
        # Whatever compose resolved must be a bare host: no scheme, no port,
        # no folded junk — it is interpolated straight into a URL template.
        assert " " not in env["EXECUTION_EXTERNAL_HOST"]
        assert "://" not in env["EXECUTION_EXTERNAL_HOST"]