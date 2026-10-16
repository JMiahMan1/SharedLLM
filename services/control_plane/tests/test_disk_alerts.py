"""Admins hear once when a data disk drops below the free-space threshold and
once when it recovers (services/control_plane/disk_alerts.py)."""
import os
from types import SimpleNamespace

from services.control_plane import disk_alerts


def test_alert_once_per_crossing_and_once_on_recovery():
    low: set[str] = set()
    assert disk_alerts.disk_transitions(low, {"data": 40.0}, 10) == []
    notices = disk_alerts.disk_transitions(low, {"data": 9.5}, 10)
    assert [n["title"] for n in notices] == ["Disk nearly full"]
    assert notices[0]["tag"] == "admin:disk:data" and "9.5% free" in notices[0]["body"]
    assert disk_alerts.disk_transitions(low, {"data": 8.0}, 10) == []
    back = disk_alerts.disk_transitions(low, {"data": 12.0}, 10)
    assert [n["title"] for n in back] == ["Disk space recovered"]
    assert back[0]["tag"] == "admin:disk:data"
    assert disk_alerts.disk_transitions(low, {"data": 12.0}, 10) == []
    assert len(disk_alerts.disk_transitions(low, {"data": 5.0}, 10)) == 1


def test_paths_on_one_filesystem_are_checked_once(monkeypatch):
    devices = {"/hostfs/data": 1, "/hostfs/workspaces": 1, "/hostfs/docker": 2}

    def stat(path):
        if path not in devices:
            raise FileNotFoundError(path)
        return SimpleNamespace(st_dev=devices[path])

    monkeypatch.setattr(disk_alerts.os, "stat", stat)
    monkeypatch.setattr(disk_alerts.os, "statvfs",
                        lambda p: SimpleNamespace(f_bavail=5, f_blocks=100) if p == "/hostfs/data"
                        else SimpleNamespace(f_bavail=50, f_blocks=100))
    free = disk_alerts.readings(["/hostfs/data", "/hostfs/workspaces", "/hostfs/docker", "/hostfs/missing"])
    assert free == {"data, workspaces": 5.0, "docker": 50.0}


def test_a_crossing_telemetry_refused_is_retried(monkeypatch):
    monkeypatch.setattr(disk_alerts, "readings", lambda paths: {"data": 4.0})
    monkeypatch.setattr(disk_alerts, "notify_admins", lambda notice: False)
    low: set[str] = set()
    assert disk_alerts.tick(low, 10) == 0 and low == set()
    sent = []
    monkeypatch.setattr(disk_alerts, "notify_admins", lambda notice: sent.append(notice) or True)
    assert disk_alerts.tick(low, 10) == 1 and low == {"data"}
    assert disk_alerts.tick(low, 10) == 0
    assert [n["title"] for n in sent] == ["Disk nearly full"]


def test_notices_go_to_admins_in_the_admin_category(monkeypatch):
    posted = {}

    def post(url, json=None, headers=None, timeout=None):
        posted.update(url=url, json=json, secret="X-Internal-Secret" in headers)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(disk_alerts.requests, "post", post)
    assert disk_alerts.notify_admins({"title": "Disk nearly full", "body": "x", "tag": "admin:disk:data"})
    assert posted["url"].endswith("/api/telemetry/notify") and posted["secret"]
    assert posted["json"]["user"] == "@admins" and posted["json"]["category"] == "admin"


def test_the_real_filesystem_reads_a_percentage():
    pct = disk_alerts.free_percent(os.path.dirname(__file__))
    assert 0.0 <= pct <= 100.0
