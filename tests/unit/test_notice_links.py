"""Every notice says where a tap goes (docs/PUSH.md, "Deep links").

``services/common/notice_links.py`` is the one place a notice's target is
built. These tests hold the contract:

- every ``notice(...)`` call in the services declares a target (``link=``, or
  the older ``url=`` that is read into one), and every route a sender names
  literally exists;
- every literal link a sender builds is one the app can open;
- telemetry refuses a notice with no target or one no page reads;
- the clients' copy of the routes (``noticeRoutes.json``) is the registry.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from services.common import notice_links, notify
from services.common.notice_links import ROUTES, LinkError, check, link, link_from_href

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"


def _source_files() -> list[Path]:
    out = []
    for path in SERVICES.rglob("*.py"):
        parts = set(path.relative_to(SERVICES).parts)
        if parts & {"tests", "test", "node_modules", "android"} or path.name.startswith("test_"):
            continue
        out.append(path)
    return sorted(out)


def _callee(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _notice_calls():
    """Every call that builds a notice with services.common.notify.notice."""
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        if "notice(" not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _callee(node) != "notice":
                continue
            func = node.func
            # notify.notice / _notify.notice / jarvis_notify.notice, or the
            # bible service's ``_notice`` alias imported from notify.
            if isinstance(func, ast.Attribute) and not (isinstance(func.value, ast.Name)
                                                         and func.value.id.endswith("notify")):
                continue
            if isinstance(func, ast.Name) and "from services.common.notify import notice" not in text:
                continue
            yield path, node


def _link_calls():
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        if "link(" not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "link"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in {"notify", "_notify", "jarvis_notify", "notice_links"}):
                yield path, node


def test_the_scan_finds_the_senders():
    calls = list(_notice_calls())
    # Chat, private chat, Beloved, family safety, screen time, guardian, garden,
    # homehub, health, geo, execution...: far more than a handful.
    assert len(calls) > 60
    files = {p.relative_to(SERVICES).as_posix() for p, _ in calls}
    for expected in ("gateway/chat_notify.py", "gateway/main.py", "gateway/notices.py", "garden/notices.py",
                     "homehub/discovery.py", "execution/handlers/timer.py", "telemetry/app_catalog.py"):
        assert expected in files


def test_every_notice_declares_where_a_tap_goes():
    missing = []
    for path, node in _notice_calls():
        names = {kw.arg for kw in node.keywords}
        if "link" in names or "url" in names or None in names:  # None: **kwargs carrying it
            continue
        missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not missing, "notices without a target (pass link=notify.link(...)):\n" + "\n".join(missing)


def test_every_literal_url_a_notice_passes_is_a_page():
    bad = []
    for path, node in _notice_calls():
        for kw in node.keywords:
            if kw.arg == "url" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                try:
                    link_from_href(kw.value.value)
                except LinkError as e:
                    bad.append(f"{path.relative_to(ROOT)}:{node.lineno}: {e}")
    assert not bad, "\n".join(bad)


def test_every_route_a_sender_names_exists_and_literal_links_open():
    bad = []
    seen = 0
    for path, node in _link_calls():
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        seen += 1
        route = node.args[0].value
        where = f"{path.relative_to(ROOT)}:{node.lineno}"
        if route not in ROUTES:
            bad.append(f"{where}: unknown route {route!r}")
            continue
        # A link whose every argument is a literal can be built here, exactly as at run time.
        if all(isinstance(kw.value, ast.Constant) for kw in node.keywords if kw.arg):
            try:
                link(route, **{kw.arg: kw.value.value for kw in node.keywords if kw.arg})
            except LinkError as e:
                bad.append(f"{where}: {e}")
        else:
            spec = ROUTES[route]
            for kw in node.keywords:
                if kw.arg in (None, "focus", "hash"):
                    continue
                placeholders = {p.strip("{}") for p in spec.path.split("/") if p.startswith("{")}
                if kw.arg not in spec.query and kw.arg not in placeholders:
                    bad.append(f"{where}: route {route!r} does not read {kw.arg!r}")
            focus = next((kw.value for kw in node.keywords if kw.arg == "focus"), None)
            if isinstance(focus, ast.JoinedStr) and focus.values and isinstance(focus.values[0], ast.Constant):
                kind = str(focus.values[0].value).split("-", 1)[0]
                if kind not in spec.focus:
                    bad.append(f"{where}: route {route!r} cannot show a {kind!r}")
    assert seen > 40
    assert not bad, "\n".join(bad)


def test_the_clients_copy_of_the_routes_is_current():
    on_disk = json.loads(notice_links.ROUTES_JSON.read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(notice_links.routes_table())), (
        "services/ui/src/lib/noticeRoutes.json is stale: run python -m services.common.notice_links")


# ---------------------------------------------------------------- the helper


def test_a_link_is_the_page_the_item_and_one_address():
    got = link("member", username="sam", section="time", focus="unlock-21")
    assert got == {
        "route": "member", "path": "/family/member/sam", "query": {"section": "time"}, "hash": "",
        "focus": "unlock-21", "guard": "parent", "href": "/family/member/sam?section=time&focus=unlock-21",
    }
    assert link_from_href(got["href"]) == got
    assert check(got) == got


@pytest.mark.parametrize(("route", "kwargs", "why"), [
    ("nowhere", {}, "unknown route"),
    ("member", {}, "needs 'username'"),
    ("member", {"username": "sam", "colour": "red"}, "does not read"),
    ("member", {"username": "sam", "section": "secrets"}, "must be one of"),
    ("member", {"username": "sam", "focus": "plant-3"}, "cannot show"),
    ("bible", {"ref": "John 3", "hash": "chapter"}, "no section"),
    ("member", {"username": "../admin"}, "not a page address part"),
    ("devices", {"hash": "nope"}, "no section"),
    ("chat", {"chat": "abc", "focus": "msg 4"}, "focus must be"),
])
def test_a_link_no_page_could_open_is_refused(route, kwargs, why):
    with pytest.raises(LinkError, match=why):
        link(route, **kwargs)


@pytest.mark.parametrize("section", ["1", "home", "days", "today", "notes", "dates", "justus"])
def test_a_beloved_notice_opens_the_section_it_is_about(section):
    got = link("chat", chat="abc", beloved=section)
    assert got["href"] == f"/family?chat=abc&beloved={section}"
    assert got["guard"] == "beloved"
    assert link_from_href(got["href"])["query"]["beloved"] == section


def test_a_beloved_notice_still_reads_as_the_plain_page():
    # ``beloved=1`` was the only value before sections existed, and it must keep meaning "the page".
    assert link("chat", chat="abc", beloved=1)["href"] == "/family?chat=abc&beloved=1"


def test_a_beloved_section_no_page_shows_is_refused():
    with pytest.raises(LinkError, match="'beloved' must be one of"):
        link("chat", chat="abc", beloved="recipes")


def test_stricter_pages_carry_their_guard():
    assert link("admin", section="ops", mission=7)["guard"] == "admin"
    assert link("chat", chat="t", beloved=1)["guard"] == "beloved"
    assert link("chat", chat="t", private=1)["guard"] == "private_chat"
    assert link("chat", chat="t")["guard"] == "member"


def test_a_bible_reminder_opens_the_chapter_at_its_verse():
    got = link("bible", ref="John 3", verse=16, hash="v16")
    assert got["href"] == "/bible?ref=John%203&verse=16#v16"
    assert link_from_href(got["href"])["hash"] == "v16"
    assert link("bible", book="John", chapter=3, verse=16)["href"] == "/bible?book=John&chapter=3&verse=16"


def test_old_addresses_are_read_into_links():
    assert link_from_href("/family")["route"] == "family"
    assert link_from_href("/family?chat=abc")["route"] == "chat"
    assert link_from_href("/family?tab=create")["route"] == "family"
    assert link_from_href("/admin/ops")["guard"] == "admin"
    for bad in ("https://example.com", "//evil", "/health", "/family?tab=secret", "/admin/root"):
        with pytest.raises(LinkError):
            link_from_href(bad)


def test_notice_needs_a_target():
    with pytest.raises(LinkError, match="needs a link"):
        notify.notice("sam", "chat", "Hi")
    with pytest.raises(LinkError):
        notify.notice("sam", "chat", "Hi", url="/communication")
    payload = notify.notice("sam", "chat", "Hi", link=link("chat", chat="t", focus="msg-9"))
    assert payload["data"]["link"]["focus"] == "msg-9"
    # Older apps follow data.url: it is the link's address.
    assert payload["data"]["url"] == "/family?chat=t&focus=msg-9"


def test_view_buttons_open_checked_pages():
    assert notify.view_action(link("family", tab="calls"))["url"] == "/family?tab=calls"
    assert notify.view_action("https://www.openstreetmap.org/x")["url"].startswith("https://")
    with pytest.raises(LinkError):
        notify.view_action("/nowhere")


# -------------------------------------------------------------- telemetry


def test_telemetry_refuses_a_notice_without_a_target():
    from services.telemetry import notices

    with pytest.raises(notices.NoticeError, match=r"data\.link"):
        notices.parse({"user": "sam", "title": "Hi"})
    with pytest.raises(notices.NoticeError, match=r"data\.link"):
        notices.parse({"user": "sam", "title": "Hi", "data": {"link": {"route": "nowhere", "path": "/x"}}})
    with pytest.raises(notices.NoticeError, match=r"data\.link"):
        notices.parse({"user": "sam", "title": "Hi", "data": {"url": "/communication"}})


def test_telemetry_fills_the_link_from_an_old_url_and_rewrites_url_from_a_link():
    from services.telemetry import notices

    fields = notices.parse({"user": "sam", "title": "Hi", "data": {"url": "/wander?person=leo"}})
    assert fields["data"]["link"]["route"] == "wander"
    target = link("admin", section="ops", mission=4, focus="mission-4")
    fields = notices.parse({"user": "sam", "title": "Hi", "data": {"link": {**target, "href": "/evil"},
                                                                    "url": "/elsewhere"}})
    assert fields["data"]["url"] == target["href"]
    assert fields["data"]["link"] == target


def test_telemetry_checks_view_buttons():
    from services.telemetry import notices

    ok = {"user": "sam", "title": "Hi", "data": {"link": link("home")}}
    with pytest.raises(notices.NoticeError, match="view action"):
        notices.parse({**ok, "actions": [{"label": "Open", "type": "view", "url": "/nowhere"}]})
    fields = notices.parse({**ok, "actions": [{"label": "Map", "type": "view", "url": "https://osm.org/x"}]})
    assert fields["actions"][0]["url"] == "https://osm.org/x"


# ------------------------------------------------- the senders' own targets


def test_a_chat_notice_opens_the_message():
    from services.gateway import chat_notify

    conv = {"token": "abc", "last_message_id": 42, "last_message": "On my way", "last_message_actor_id": "alex",
            "last_message_actor": "Alex", "unread_messages": 1, "type": 1}
    payload = chat_notify.notice_for(conv, "sam", "sam")
    assert payload["data"]["link"]["href"] == "/family?chat=abc&focus=msg-42"
    assert payload["actions"][1]["url"] == "/family?chat=abc&focus=msg-42"
    private = chat_notify.notice_for(conv, "sam", "sam", private=True)
    assert private["data"]["link"]["guard"] == "private_chat"
    assert private["data"]["link"]["focus"] == "msg-42"


def test_a_concern_opens_on_the_childs_page_at_the_concern():
    from services.telemetry import guardian, notices

    child = {"username": "jordan", "display_name": "Jordan"}
    concern = {"id": "c9", "category": "bullying", "level": "severe", "label": "Bullying", "excerpt": "..."}
    notice = guardian._concern_notice("parent", child, concern, "2026-11-21")
    assert notice["data"]["link"]["href"] == "/family/member/jordan?focus=concern-c9"
    assert notices.parse(notice)["data"]["link"]["guard"] == "parent"


def test_home_alerts_open_the_camera_or_the_devices_history():
    from services.execution.home_alerts import alert_link

    assert alert_link("doorbell", "binary_sensor.door")["route"] == "cameras"
    assert alert_link("water_leak", "binary_sensor.leak")["href"] == "/history?e=binary_sensor.leak"


def test_a_garden_digest_opens_the_plant():
    from services.garden.notices import build_digest

    one = build_digest("sam", [{"plant_id": 5, "nickname": "Fern", "action": "water"}])
    assert one["link"]["href"] == "/garden/plants/5"
    two = build_digest("sam", [{"plant_id": 5, "action": "water"}, {"plant_id": 6, "action": "water"}])
    assert two["link"]["href"] == "/garden?focus=plant-5"


def test_an_automation_notice_opens_its_page_or_the_entity():
    from services.homehub.drivers.mobile_app import _tap_link

    assert _tap_link({"url": "/cameras"})["route"] == "cameras"
    assert _tap_link({"url": "/lovelace/0", "entity_id": "lock.front"})["href"] == "/history?e=lock.front"
    assert _tap_link({"clickAction": "https://example.com"})["route"] == "home"


def test_a_timer_from_an_old_address_still_notifies():
    from services.execution.handlers.timer import timer_link

    assert timer_link("/notes?note=n1")["href"] == "/notes?note=n1"
    assert timer_link("/communication")["href"] == "/family?tab=announce"
    assert timer_link("/family?tab=announce")["href"] == "/family?tab=announce"
