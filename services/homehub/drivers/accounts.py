"""Accounts other services use: entries that hold a login and drive nothing.

The app vetting account (``common_sense_media``; docs/APP_VETTING.md) is set
up on the Devices page like any cloud account, but the lookups run in
telemetry, which reads the entry through
``GET /hub/integrations/{domain}``. The hub only keeps the entry, so its
driver has nothing to connect to and no entities: it loads at once, so the
Integrations list shows the account as working instead of "no driver yet".
"""
from __future__ import annotations

from services.homehub.core import Driver


class AccountDriver(Driver):
    poll_interval = None  # nothing to refresh: the entry is the whole integration
