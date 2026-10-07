"""Extending the road map to where trips go, and deploying a rebuilt map."""
from datetime import datetime, timezone

from services.control_plane import osrm_map

FILE = """# comment
https://download.geofabrik.de/north-america/us/arizona-latest.osm.pbf
"""
NM = {"name": "New Mexico", "url": "https://download.geofabrik.de/north-america/us/new-mexico-latest.osm.pbf"}
AZ = {"name": "Arizona", "url": "https://download.geofabrik.de/north-america/us/arizona-latest.osm.pbf"}


def test_only_missing_geofabrik_regions_are_added():
    evil = {"name": "x", "url": "https://evil.example/x.osm.pbf"}
    assert osrm_map.missing_regions(FILE, [AZ, NM, NM, evil]) == [NM]


def test_extend_commits_the_missing_region(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("OSRM_MAP_BRANCH", "microservices")
    monkeypatch.setattr(osrm_map, "regions_file", lambda repo, branch, token: (FILE, "blobsha"))
    sent = {}

    class Resp:
        def raise_for_status(self):
            pass

    def put(url, headers=None, timeout=None, data=None):
        import json
        sent["url"], sent["body"] = url, json.loads(data)
        return Resp()
    monkeypatch.setattr(osrm_map.requests, "put", put)
    assert osrm_map.extend([NM], "tok") == ["New Mexico"]
    assert sent["url"].endswith("/repos/owner/repo/contents/osrm/regions.txt")
    assert sent["body"]["branch"] == "microservices" and sent["body"]["sha"] == "blobsha"
    import base64
    assert NM["url"] in base64.b64decode(sent["body"]["content"]).decode()


def test_nothing_happens_without_config(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("OSRM_MAP_BRANCH", raising=False)
    assert osrm_map.extend([NM], "tok") == []


def test_deploy_only_after_a_newer_build():
    built = datetime(2026, 10, 7, 6, 0, tzinfo=timezone.utc)
    assert osrm_map.needs_deploy("2026-10-07T05:00:00.123456789Z", built)
    assert not osrm_map.needs_deploy("2026-10-07T07:00:00.1Z", built)
    assert not osrm_map.needs_deploy("2026-10-07T05:00:00Z", None)
