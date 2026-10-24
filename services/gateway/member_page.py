"""What the family member page leaves out for a mother (docs/FAMILY_MEMBER_PAGE.md).

A child's page is open to the people identity's ``may_view_raw_data`` allows
(an admin, the father, the linked mother). Of those, only an admin and the
father see the technical side (identity's ``may_view_technical_data``); for
everyone else the gateway strips it from every response the page reads, so
the page can only hide what the server already withheld.

Technical here means facts about the machinery, not the child: device ids
and keys, OS and app versions and builds, network addresses, guard,
permission and service states, sync sources and timestamps, tokens and the
model that reviewed a concern. Package names stay: every app control (block,
daily minutes, approve an install) is keyed by one, and the page shows the
app's name, never the package.
"""
from __future__ import annotations

from typing import Any

TECHNICAL_KEYS = frozenset({
    # devices
    "device", "device_key", "device_id", "devices", "entity_id", "model", "manufacturer", "hardware",
    "os_version", "os_build", "app_version", "app_build", "esphome_version", "capabilities",
    # network
    "ip", "ip_address", "last_ip_address", "network", "ssid",
    # guard, permission and service states
    "protected", "app_approvals", "permission_approvals", "guard", "permissions",
    # sync
    "source", "sources", "updated_at", "synced_at", "last_sync_at", "last_synced",
    # secrets
    "token", "push_token", "api_key",
})


def strip_technical(value: Any) -> Any:
    """``value`` with every technical key removed, at any depth."""
    if isinstance(value, dict):
        return {k: strip_technical(v) for k, v in value.items() if k not in TECHNICAL_KEYS}
    if isinstance(value, list):
        return [strip_technical(v) for v in value]
    return value
