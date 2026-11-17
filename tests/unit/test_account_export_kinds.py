"""Every kind of record an account export can hold is listed in the viewer's kinds.json.

The viewer (services/ui/src/lib/accountExport/present.ts) has a formatter per
kind, and its vitest test checks every kind in kinds.json has one. This test
keeps kinds.json equal to what the services really export, so a table added
to an export without a formatter fails here or there.
"""
import asyncio
import json
from pathlib import Path

from services.bible.account_export import specs as bible_specs
from services.geo import account_export as geo
from services.health.account_export import specs as health_specs
from services.identity.account_export import specs as identity_specs

KINDS = Path(__file__).resolve().parents[2] / "services/ui/src/lib/accountExport/kinds.json"


class _EmptyRedis:
    async def hgetall(self, key):
        return {}

    async def get(self, key):
        return None

    async def zrange(self, key, start, end):
        return []


def test_kinds_json_matches_what_the_services_export():
    geo_kinds = [*asyncio.run(geo.export_user(_EmptyRedis(), "someone")).keys(), "location_history"]
    exported = {
        "identity": [s.name for s in identity_specs()],
        "health": [s.name for s in health_specs()],
        "bible": [s.name for s in bible_specs()],
        "geo": geo_kinds,
    }
    listed = json.loads(KINDS.read_text())
    assert {k: sorted(v) for k, v in listed.items()} == {k: sorted(v) for k, v in exported.items()}
