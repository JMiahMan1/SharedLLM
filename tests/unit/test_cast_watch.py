"""scripts/cast_watch.py: the read-only Cast watcher's pure parts. Addresses and MACs are made up."""
import importlib.util
import sqlite3
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("cast_watch", Path(__file__).parents[2] / "scripts" / "cast_watch.py")
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)


def test_a_snapshot_change_and_an_alert():
    cs = SimpleNamespace(app_id="AndroidNativeApp", display_name="Spotify", is_stand_by=True)
    ms = SimpleNamespace(player_state="PLAYING", title="A song", artist="A band", content_id="x", current_time=3.0)
    snap = cw.cast_snapshot(cs, ms)
    assert snap["app"] == "Spotify" and snap["standby"] is True and snap["title"] == "A song"
    assert cw.should_alert(snap, {"AndroidNativeApp"})
    assert not cw.should_alert({**snap, "app_id": "CC1AD845"}, {"AndroidNativeApp"})
    assert not cw.should_alert({**snap, "title": None}, {"AndroidNativeApp"})
    assert cw.change_key(snap) != cw.change_key({**snap, "title": "Another"})


def test_connect_info_leaves_out_keys_and_ids():
    out = cw.public_connect_info({"remoteName": "TV", "activeUser": "", "publicKey": "k", "deviceID": "d", "clientID": "c"})
    assert out == {"remoteName": "TV", "activeUser": ""}


def test_the_router_view_lists_spotify_lookups_and_wifi_lines(tmp_path):
    db_path = tmp_path / "router.db"
    db = sqlite3.connect(db_path)
    db.execute("create table dns_hourly (hour, device, ip, domain, count, last)")
    db.execute("create table router_log (id integer primary key, time, level, source, msg)")
    db.executemany("insert into dns_hourly values (?,?,?,?,?,?)", [
        ("h", "02:00:00:00:00:01", "192.0.2.10", "spclient.wg.spotify.com", 2, 1000.0),
        ("h", "02:00:00:00:00:02", "192.0.2.11", "example.com", 1, 1000.0),
        ("h", "02:00:00:00:00:03", "192.0.2.12", "i.scdn.co", 1, 10.0),
    ])
    db.executemany("insert into router_log (time, level, source, msg) values (?,?,?,?)", [
        (1000.0, "info", "hostapd", "AP-STA-CONNECTED 02:00:00:00:00:01"),
        (1000.0, "info", "kernel", "noise"),
    ])
    db.commit()
    view = cw.router_view(str(db_path), 500.0)
    assert [x["device"] for x in view["spotify_lookups"]] == ["02:00:00:00:00:01"]
    assert [x["source"] for x in view["wifi_and_dhcp"]] == ["hostapd"]
    n = cw.notice_for({"cast": {"app": "Spotify", "state": "PLAYING", "title": "A song", "standby": True},
                       "spotify_connect": {"activeUser": ""}, "router": view})
    assert "02:00:00:00:00:01" in n["body"] and "no account named" in n["body"]
