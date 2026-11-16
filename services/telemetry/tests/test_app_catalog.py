"""App vetting in telemetry: inventories, Play lookups with backoff, the local
review, and who hears about flagged apps."""
import json
from pathlib import Path

import pytest
from fakes import FakeRedis
from fastapi.testclient import TestClient

from services.telemetry import app_catalog, guardian, llm
from services.telemetry.config import INTERNAL_SECRET as EXPECTED_SECRET
from services.telemetry.main import app

HEADERS = {"X-Internal-Secret": EXPECTED_SECRET}
FIXTURES = Path(__file__).resolve().parents[2] / "common" / "tests" / "fixtures" / "play"
# Age 10 (Everyone 10+); one parent hears everything, the other only severe.
KIDS = {"kid": {"username": "kid", "display_name": "Sam", "parents": ["dad", "mom"], "monitoring": "high",
                "notify_levels": {"mom": "severe"}, "device_kinds": ["phone"], "birth_date": "2016-01-01",
                "app_policy": {}}}
REVIEW = {"summary": "A building game with open chat.", "concerns": [
    {"category": "contact_strangers", "severity": "high", "why": "Strangers can message players.", "evidence": []}]}


LABELS = {"com.roblox.client": "Roblox", "com.kiloo.subwaysurf": "Subway Surfers"}


def _rpc(rows):
    """A made-up Play reviews answer (batchexecute) holding ``rows``."""
    inner = json.dumps([rows, None])
    return ")]}'\n\n" + json.dumps([["wrb.fr", "UsvDTd", inner, None, None, None, "generic"]])


def _row(stars, text):
    return ["id", ["Made-up Reviewer", None], stars, None, text, [1_799_000_000, 0], 0, None, None, None, "1.0"]


def _page(package):
    return (FIXTURES / f"{package}.html").read_text(encoding="utf-8")


@pytest.fixture
def wired(monkeypatch):
    fake = FakeRedis()
    sent: list[dict] = []
    fetched: list[str] = []
    pages: dict[str, tuple[int, str]] = {}
    model: dict = {"settings": {"llm_local_url": "http://llm.local", "guardian_monitor_model": "local-model"},
                   "answer": json.dumps(REVIEW), "calls": 0}
    clock = {"now": 1_800_000_000.0}
    reviews: dict[tuple[str, str], tuple[int, str]] = {}
    review_calls: list[tuple[str, str]] = []
    csm: dict[str, tuple[int, str, str | None]] = {}
    csm_calls: list[str] = []
    mb: dict[str, tuple[int, object]] = {}
    mb_calls: list[tuple[str, str]] = []
    # Common Sense off and no MalwareBazaar key unless a test turns them on.
    checks = {"settings": {"common_sense": False, "malwarebazaar_key": ""}}

    async def _children(_session):
        return KIDS

    async def _deliver(body):
        sent.append(body)
        return {"status": "SUCCESS"}

    async def _fetch(package):
        fetched.append(package)
        if package not in pages:
            raise AssertionError(f"unexpected lookup of {package}")
        return pages[package]

    async def _settings(_session):
        return model["settings"]

    async def _reviews(package, batch):
        review_calls.append((package, batch))
        return reviews.get((package, batch), (200, _rpc([])))

    async def _csm(url):
        csm_calls.append(url)
        return csm.get(url, (404, "", None))

    async def _mb(sha, key):
        mb_calls.append((sha, key))
        if sha not in mb:
            return 200, {"query_status": "hash_not_found"}
        answer = mb[sha]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def _check_settings(_session):
        if isinstance(checks["settings"], Exception):
            raise checks["settings"]
        return checks["settings"]

    async def _chat(session, url, payload, timeout_s, headers=None, what="the model"):
        model["calls"] += 1
        if isinstance(model["answer"], Exception):
            raise model["answer"]
        return model["answer"]

    monkeypatch.setattr("services.telemetry.main._redis", lambda: fake)
    monkeypatch.setattr("services.telemetry.main.deliver_notice", _deliver)
    monkeypatch.setattr(guardian, "children", _children)
    monkeypatch.setattr(guardian, "_model_settings", _settings)
    monkeypatch.setattr(guardian, "household_now", lambda: __import__("datetime").datetime(2026, 11, 15, 12, 0))
    monkeypatch.setattr(app_catalog, "fetch_page", _fetch)
    monkeypatch.setattr(app_catalog, "fetch_reviews", _reviews)
    monkeypatch.setattr(app_catalog, "fetch_common_sense", _csm)
    monkeypatch.setattr(app_catalog, "query_malwarebazaar", _mb)
    monkeypatch.setattr(app_catalog, "check_settings", _check_settings)
    monkeypatch.setattr(app_catalog, "_now", lambda: clock["now"])
    monkeypatch.setattr(llm, "chat_content", _chat)
    with TestClient(app) as client:
        yield {"client": client, "redis": fake, "sent": sent, "fetched": fetched, "pages": pages,
               "model": model, "clock": clock, "deliver": _deliver, "reviews": reviews,
               "review_calls": review_calls, "csm": csm, "csm_calls": csm_calls, "mb": mb, "mb_calls": mb_calls,
               "checks": checks}


def _inventory(*packages, device="phone-key-0000000001", usage_access=True):
    return {"user": "kid", "device": device, "usage_access": usage_access, "apps": [
        {"package": p, "label": LABELS.get(p, p.split(".")[-1].title()), "installer": "com.android.vending",
         "first_install": 1_700_000_000_000, "last_used": 1_799_000_000_000, "permissions": []} for p in packages]}


async def _drain(w):
    """Run the loop's steps until there is nothing left to do now."""
    for _ in range(20):
        outcome = await app_catalog.step(w["redis"], None, w["deliver"])
        w["clock"]["now"] += app_catalog.APP_CATALOG_MIN_INTERVAL_S
        if outcome == "idle" and not await w["redis"].zrangebyscore(app_catalog.LOOKUP_DUE, 0, w["clock"]["now"]):
            return


def test_an_inventory_is_kept_per_device_and_queues_lookups(wired):
    client = wired["client"]
    r = client.post("/api/telemetry/apps/inventory", headers=HEADERS, json=_inventory("com.roblox.client", "com.jarvisos.app"))
    assert r.status_code == 200, r.text
    assert r.json()["apps"] == 1  # Jarvis itself is never listed
    assert r.json()["queued"] == ["com.roblox.client"]
    client.post("/api/telemetry/apps/inventory", headers=HEADERS,
                json=_inventory("com.roblox.client", "com.kiloo.subwaysurf", device="tablet-key-000000001"))
    data = client.get("/api/telemetry/apps/kid", headers=HEADERS).json()
    assert {d["device"] for d in data["devices"]} == {"phone-key-0000000001", "tablet-key-000000001"}
    roblox = next(a for a in data["apps"] if a["package"] == "com.roblox.client")
    assert sorted(roblox["devices"]) == ["phone-key-0000000001", "tablet-key-000000001"]
    assert data["catalog"]["com.roblox.client"]["listing"]["status"] == "pending"
    assert wired["fetched"] == []  # nothing goes to Google while answering


def test_routes_need_the_internal_secret(wired):
    client = wired["client"]
    assert client.post("/api/telemetry/apps/inventory", json=_inventory("com.x.y")).status_code == 401
    assert client.get("/api/telemetry/apps/kid").status_code == 401


async def test_a_found_listing_is_reviewed_and_the_parents_hear_at_their_level(wired):
    wired["pages"]["com.roblox.client"] = (200, _page("com.roblox.client"))
    await app_catalog.save_inventory(wired["redis"], "kid", "phone-key-0000000001", _inventory("com.roblox.client"))
    await _drain(wired)
    assert wired["fetched"] == ["com.roblox.client"]
    review = json.loads(await wired["redis"].get(f"{app_catalog.REVIEW_PREFIX}com.roblox.client"))
    assert review["status"] == "ready" and review["concerns"][0]["category"] == "contact_strangers"
    # Teen is above an age-10 limit and chat with strangers is high: dad (high) hears, mom (severe only) does not.
    assert [n["user"] for n in wired["sent"]] == ["dad"]
    notice = wired["sent"][0]
    assert notice["audience"] == "parents" and notice["kind"] == "app_flag"
    assert notice["title"] == "Roblox on Sam's phone"
    assert "Rated Teen, above the Everyone 10+ limit" in notice["body"]
    # Once per app and verdict: another pass says nothing new.
    await app_catalog.notify_child(wired["redis"], KIDS["kid"], wired["deliver"])
    assert len(wired["sent"]) == 1


async def test_not_on_play_is_kept_and_judged_on_permissions(wired):
    wired["pages"]["com.private.tool"] = (404, "")
    body = _inventory("com.private.tool")
    body["apps"][0].update(installer=None, permissions=["location", "microphone"])
    await app_catalog.save_inventory(wired["redis"], "kid", "phone-key-0000000001", body)
    await _drain(wired)
    entry = await app_catalog.catalog_entry(wired["redis"], "com.private.tool")
    assert entry["listing"]["status"] == "not_on_play" and entry["review"] is None
    assert wired["model"]["calls"] == 0
    assert [n["user"] for n in wired["sent"]] == ["dad"]
    assert "Not on Google Play" in wired["sent"][0]["body"]


async def test_google_refusing_backs_off_and_keeps_the_package_queued(wired):
    wired["pages"]["com.roblox.client"] = (429, "")
    await app_catalog.queue_lookups(wired["redis"], ["com.roblox.client"])
    assert await app_catalog.step(wired["redis"], None, wired["deliver"]) == "fetched"
    state = await app_catalog.backoff_state(wired["redis"])
    assert state and state["failures"] == 1 and "429" in state["reason"]
    assert await wired["redis"].zrangebyscore(app_catalog.LOOKUP_DUE, 0, wired["clock"]["now"]) == ["com.roblox.client"]
    # While backing off nothing is fetched, however long the queue.
    wired["clock"]["now"] += app_catalog.APP_CATALOG_BACKOFF_BASE_S - 1
    assert await app_catalog.step(wired["redis"], None, wired["deliver"]) == "idle"
    assert wired["fetched"] == ["com.roblox.client"]
    # A second failure doubles the wait.
    wired["clock"]["now"] += 2
    await app_catalog.step(wired["redis"], None, wired["deliver"])
    state = json.loads(await wired["redis"].get(app_catalog.BACKOFF_KEY))
    assert state["failures"] == 2 and state["until"] - wired["clock"]["now"] == 2 * app_catalog.APP_CATALOG_BACKOFF_BASE_S
    # Then a success clears it.
    wired["pages"]["com.roblox.client"] = (200, _page("com.roblox.client"))
    wired["clock"]["now"] += 3 * app_catalog.APP_CATALOG_BACKOFF_BASE_S
    await app_catalog.step(wired["redis"], None, wired["deliver"])
    assert await wired["redis"].get(app_catalog.BACKOFF_KEY) is None


async def test_lookups_are_spaced_by_the_minimum_interval(wired):
    for pkg in ("com.roblox.client", "com.kiloo.subwaysurf"):
        wired["pages"][pkg] = (200, _page(pkg))
    await app_catalog.queue_lookups(wired["redis"], ["com.roblox.client", "com.kiloo.subwaysurf"])
    assert await app_catalog.step(wired["redis"], None, wired["deliver"]) == "fetched"
    wired["clock"]["now"] += 1
    assert await app_catalog.step(wired["redis"], None, wired["deliver"]) == "idle"
    assert len(wired["fetched"]) == 1
    wired["clock"]["now"] += app_catalog.APP_CATALOG_MIN_INTERVAL_S
    assert await app_catalog.step(wired["redis"], None, wired["deliver"]) == "fetched"
    assert len(wired["fetched"]) == 2


async def test_a_cached_listing_is_not_fetched_again_until_it_is_old(wired):
    wired["pages"]["com.kiloo.subwaysurf"] = (200, _page("com.kiloo.subwaysurf"))
    await app_catalog.queue_lookups(wired["redis"], ["com.kiloo.subwaysurf"])
    await _drain(wired)
    assert await app_catalog.queue_lookups(wired["redis"], ["com.kiloo.subwaysurf"]) == []
    wired["clock"]["now"] += app_catalog.APP_CATALOG_TTL_DAYS * 86400
    assert await app_catalog.queue_lookups(wired["redis"], ["com.kiloo.subwaysurf"]) == ["com.kiloo.subwaysurf"]


async def test_a_page_the_parser_cannot_read_is_an_error_with_its_reason(wired):
    wired["pages"]["com.roblox.client"] = (200, "<html>Something else entirely</html>")
    await app_catalog.queue_lookups(wired["redis"], ["com.roblox.client"])
    await app_catalog.step(wired["redis"], None, wired["deliver"])
    entry = await app_catalog.catalog_entry(wired["redis"], "com.roblox.client")
    assert entry["listing"]["status"] == "error" and entry["listing"]["reason"]


async def test_bad_model_output_is_a_failed_review_that_is_retried(wired):
    wired["model"]["answer"] = "Looks fine to me!"
    wired["pages"]["com.roblox.client"] = (200, _page("com.roblox.client"))
    await app_catalog.queue_lookups(wired["redis"], ["com.roblox.client"])
    await app_catalog.step(wired["redis"], None, wired["deliver"])
    review = json.loads(await wired["redis"].get(f"{app_catalog.REVIEW_PREFIX}com.roblox.client"))
    assert review["status"] == "failed" and "JSON" in review["reason"] and review["attempts"] == 1
    assert "concerns" not in review
    retry_at = wired["redis"].zsets[app_catalog.REVIEW_DUE]["com.roblox.client"]
    assert retry_at > wired["clock"]["now"]
    wired["model"]["answer"] = json.dumps(REVIEW)
    wired["clock"]["now"] = retry_at
    assert await app_catalog.step(wired["redis"], None, wired["deliver"]) == "reviewed"
    review = json.loads(await wired["redis"].get(f"{app_catalog.REVIEW_PREFIX}com.roblox.client"))
    assert review["status"] == "ready"


async def test_without_a_model_the_review_says_which_setting_is_missing(wired):
    wired["model"]["settings"] = {}
    wired["pages"]["com.roblox.client"] = (200, _page("com.roblox.client"))
    await app_catalog.queue_lookups(wired["redis"], ["com.roblox.client"])
    await app_catalog.step(wired["redis"], None, wired["deliver"])
    review = json.loads(await wired["redis"].get(f"{app_catalog.REVIEW_PREFIX}com.roblox.client"))
    assert review["status"] == "no_model" and "guardian_monitor_model" in review["reason"]


async def test_a_waiting_request_gets_its_notice_again_with_the_verdict(wired):
    client = wired["client"]
    wired["pages"]["com.roblox.client"] = (200, _page("com.roblox.client"))
    r = client.post("/api/telemetry/apps/catalog", headers=HEADERS, json={
        "packages": ["com.roblox.client"], "waiting": {"child": "kid", "request_id": 7, "label": "Roblox"}})
    assert r.json()["queued"] == ["com.roblox.client"]
    assert r.json()["catalog"]["com.roblox.client"]["listing"]["status"] == "pending"
    await app_catalog.step(wired["redis"], None, wired["deliver"])
    requests = [n for n in wired["sent"] if n["title"] == "New app to approve"]
    assert [n["user"] for n in requests] == ["dad", "mom"]
    notice = requests[0]
    assert notice["tag"] == "guard-app:kid:com.roblox.client" and notice["replace"] is True
    assert "Rated Teen, above the Everyone 10+ limit" in notice["body"]
    assert notice["actions"][0]["params"] == {"id": 7, "approve": True}
    # Answered once: the next refresh does not send it again.
    assert await wired["redis"].get(f"{app_catalog.WAITING_PREFIX}com.roblox.client") is None


async def test_a_request_for_a_known_app_does_not_wait(wired):
    redis = wired["redis"]
    wired["pages"]["com.kiloo.subwaysurf"] = (200, _page("com.kiloo.subwaysurf"))
    await app_catalog.queue_lookups(redis, ["com.kiloo.subwaysurf"])
    await app_catalog.step(redis, None, wired["deliver"])
    r = wired["client"].post("/api/telemetry/apps/catalog", headers=HEADERS, json={
        "packages": ["com.kiloo.subwaysurf"], "waiting": {"child": "kid", "request_id": 8}})
    assert r.json()["catalog"]["com.kiloo.subwaysurf"]["review"]["status"] == "ready"
    assert await redis.get(f"{app_catalog.WAITING_PREFIX}com.kiloo.subwaysurf") is None


async def test_many_new_flags_at_once_are_one_notice(wired):
    packages = [f"com.example.game{i}" for i in range(5)]
    for pkg in packages:
        await wired["redis"].set(f"{app_catalog.LISTING_PREFIX}{pkg}", json.dumps(
            {"status": "found", "hash": "h", "fetched_ts": wired["clock"]["now"],
             "listing": {"title": pkg, "content_rating": "Mature 17+"}}))
    await app_catalog.save_inventory(wired["redis"], "kid", "phone-key-0000000001", _inventory(*packages))
    sent = await app_catalog.notify_child(wired["redis"], KIDS["kid"], wired["deliver"])
    assert sent == 1
    assert wired["sent"][0]["title"] == "5 apps on Sam's phone need a look"


async def test_lookups_switched_off_queue_nothing_and_say_so(wired, monkeypatch):
    monkeypatch.setattr(app_catalog, "APP_CATALOG_ENABLED", False)
    assert await app_catalog.queue_lookups(wired["redis"], ["com.roblox.client"]) == []
    entry = await app_catalog.catalog_entry(wired["redis"], "com.roblox.client")
    assert entry["listing"]["status"] == "off" and "APP_CATALOG_ENABLED" in entry["listing"]["reason"]
    assert await app_catalog.step(wired["redis"], None, wired["deliver"]) == "idle"


def test_the_screen_time_reports_count_as_use(wired, monkeypatch):
    from services.telemetry import store

    async def usage(rc, user, days):
        return [{"day": "2026-11-14", "apps": [{"package": "com.roblox.client", "last_use": 1_799_500_000_000}]}]

    monkeypatch.setattr(store, "get_usage", usage)
    client = wired["client"]
    client.post("/api/telemetry/apps/inventory", headers=HEADERS, json=_inventory("com.roblox.client"))
    data = client.get("/api/telemetry/apps/kid", headers=HEADERS).json()
    assert data["apps"][0]["last_used"] == 1_799_500_000_000


# --- more reviews: the newest and the lowest rated ---------------------------

CSM_FIXTURE = Path(__file__).resolve().parents[2] / "common" / "tests" / "fixtures" / "common_sense" / "rail-dash-runner.html"


async def _step(w, seconds=None):
    outcome = await app_catalog.step(w["redis"], None, w["deliver"])
    w["clock"]["now"] += app_catalog.APP_CATALOG_MIN_INTERVAL_S if seconds is None else seconds
    return outcome


async def test_the_newest_and_lowest_reviews_are_read_then_the_review_is_redone(wired):
    pkg = "com.roblox.client"
    wired["pages"][pkg] = (200, _page(pkg))
    wired["reviews"][(pkg, "one_star")] = (200, _rpc([_row(1, "A stranger asked my son where he lives.")]))
    wired["reviews"][(pkg, "newest")] = (200, _rpc([_row(5, "Fun with my cousins.")]))
    await app_catalog.queue_lookups(wired["redis"], [pkg])
    assert await _step(wired, 1) == "fetched"  # the listing, reviewed at once with Play's first page
    assert wired["model"]["calls"] == 1 and wired["review_calls"] == []
    # The extra pages wait for the same interval as any Play request.
    assert await _step(wired, app_catalog.APP_CATALOG_MIN_INTERVAL_S - 1) == "idle"
    for _ in range(3):
        assert await _step(wired) == "fetched"
    assert wired["review_calls"] == [(pkg, "newest"), (pkg, "one_star"), (pkg, "two_star")]
    entry = json.loads(await wired["redis"].get(f"{app_catalog.LISTING_PREFIX}{pkg}"))
    assert entry["hash"] != entry["page_hash"] and entry["more_reviews"]["pending"] == []
    # Redone once with every page in: the model read the one-star review.
    assert wired["model"]["calls"] == 2
    card = await app_catalog.catalog_entry(wired["redis"], pkg)
    assert card["review"]["listing_hash"] == entry["hash"]
    assert card["listing"]["listing"]["reviews_read"] == {"relevant": 20, "newest": 1, "lowest": 1, "state": "done",
                                                          "errors": {}}
    assert "more_reviews" not in card["listing"]
    assert "Made-up Reviewer" not in json.dumps(entry)


async def test_the_lowest_reviews_reach_the_model(wired, monkeypatch):
    pkg = "com.kiloo.subwaysurf"
    wired["pages"][pkg] = (200, _page(pkg))
    wired["reviews"][(pkg, "two_star")] = (200, _rpc([_row(2, "Someone in the chat asked for photos.")]))
    seen = []

    async def _chat(session, url, payload, timeout_s, headers=None, what="the model"):
        seen.append(payload["messages"][1]["content"])
        return json.dumps(REVIEW)

    monkeypatch.setattr(llm, "chat_content", _chat)
    await app_catalog.queue_lookups(wired["redis"], [pkg])
    for _ in range(5):
        await _step(wired)
    assert "Someone in the chat asked for photos." in seen[-1] and "Lowest-rated reviews" in seen[-1]


async def test_a_refused_reviews_request_backs_off_and_keeps_its_page_pending(wired):
    pkg = "com.roblox.client"
    wired["pages"][pkg] = (200, _page(pkg))
    wired["reviews"][(pkg, "newest")] = (429, "")
    await app_catalog.queue_lookups(wired["redis"], [pkg])
    await _step(wired)
    await _step(wired)
    state = await app_catalog.backoff_state(wired["redis"])
    assert state and "reviews" in state["reason"]
    entry = json.loads(await wired["redis"].get(f"{app_catalog.LISTING_PREFIX}{pkg}"))
    assert entry["more_reviews"]["pending"][0] == "newest"


async def test_an_unreadable_reviews_page_is_kept_with_its_reason(wired):
    pkg = "com.roblox.client"
    wired["pages"][pkg] = (200, _page(pkg))
    wired["reviews"][(pkg, "two_star")] = (200, "<html>not reviews</html>")
    await app_catalog.queue_lookups(wired["redis"], [pkg])
    for _ in range(4):
        await _step(wired)
    read = (await app_catalog.catalog_entry(wired["redis"], pkg))["listing"]["listing"]["reviews_read"]
    assert read["state"] == "done" and "two_star" in read["errors"]


# --- Common Sense Media -------------------------------------------------------


def _csm_page(name="Roblox", age=13):
    return (CSM_FIXTURE.read_text(encoding="utf-8").replace("Rail Dash Runner", name)
            .replace('"typicalAgeRange": "9+"', f'"typicalAgeRange": "{age}+"').replace("age 9+", f"age {age}+"))


async def _found_listing(w, pkg="com.roblox.client"):
    w["pages"][pkg] = (200, _page(pkg))
    await app_catalog.queue_lookups(w["redis"], [pkg])
    await _step(w)


async def test_common_sense_is_found_by_the_apps_name_and_judges_the_age(wired):
    wired["checks"]["settings"] = {"common_sense": True}
    url = "https://www.commonsensemedia.org/app-reviews/roblox"
    wired["csm"][url] = (200, _csm_page("Roblox", 13), None)
    await _found_listing(wired)
    entry = await app_catalog.catalog_entry(wired["redis"], "com.roblox.client")
    assert wired["csm_calls"] == [url]
    assert entry["common_sense"]["status"] == "found" and entry["common_sense"]["review"]["age"] == 13
    verdict = app_catalog.app_vetting.assess({"package": "com.roblox.client"}, entry, app_catalog._policy(KIDS["kid"]))
    # Common Sense 13+ ties Play's Teen (13): Play's rating speaks, and it is over the age-10 limit.
    assert verdict["age_source"] == "play" and verdict["common_sense"]["age"] == 13


async def test_common_sense_follows_a_renamed_slug_and_tries_the_game_section(wired):
    wired["checks"]["settings"] = {"common_sense": True}
    wired["csm"]["https://www.commonsensemedia.org/app-reviews/roblox"] = (301, "", "/game-reviews/roblox-online")
    wired["csm"]["https://www.commonsensemedia.org/game-reviews/roblox-online"] = (200, _csm_page("Roblox", 13), None)
    await _found_listing(wired)
    for _ in range(3):
        await _step(wired, app_catalog.COMMON_SENSE_MIN_INTERVAL_S)
    assert wired["csm_calls"][:2] == ["https://www.commonsensemedia.org/app-reviews/roblox",
                                      "https://www.commonsensemedia.org/game-reviews/roblox-online"]
    entry = await app_catalog.common_sense_entry(wired["redis"], "com.roblox.client")
    assert entry["status"] == "found" and entry["review"]["kind"] == "game"


async def test_no_review_under_the_name_is_not_found_after_a_bounded_search(wired):
    wired["checks"]["settings"] = {"common_sense": True}
    await _found_listing(wired)
    for _ in range(8):
        await _step(wired, app_catalog.COMMON_SENSE_MIN_INTERVAL_S)
    assert len(wired["csm_calls"]) <= app_catalog.common_sense.MAX_CANDIDATES
    entry = await app_catalog.common_sense_entry(wired["redis"], "com.roblox.client")
    assert entry["status"] == "not_found" and "no review" in entry["reason"]


async def test_a_page_for_another_app_is_skipped(wired):
    wired["checks"]["settings"] = {"common_sense": True}
    wired["csm"]["https://www.commonsensemedia.org/app-reviews/roblox"] = (200, _csm_page("Robot Lox Builder"), None)
    await _found_listing(wired)
    for _ in range(4):
        await _step(wired, app_catalog.COMMON_SENSE_MIN_INTERVAL_S)
    assert (await app_catalog.common_sense_entry(wired["redis"], "com.roblox.client"))["status"] == "not_found"


async def test_an_unreadable_common_sense_page_is_an_error_with_its_reason(wired):
    wired["checks"]["settings"] = {"common_sense": True}
    wired["csm"]["https://www.commonsensemedia.org/app-reviews/roblox"] = (200, "<html>redesigned</html>", None)
    await _found_listing(wired)
    entry = await app_catalog.common_sense_entry(wired["redis"], "com.roblox.client")
    assert entry["status"] == "error" and "could not be read" in entry["reason"]


async def test_common_sense_failing_backs_off_on_its_own(wired):
    wired["checks"]["settings"] = {"common_sense": True}
    wired["csm"]["https://www.commonsensemedia.org/app-reviews/roblox"] = (503, "", None)
    await _found_listing(wired)
    state = await app_catalog.backoff_state(wired["redis"], app_catalog.CSM_BACKOFF_KEY)
    assert state and "503" in state["reason"]
    assert await app_catalog.backoff_state(wired["redis"]) is None  # Play goes on
    calls = len(wired["csm_calls"])
    await _step(wired, app_catalog.COMMON_SENSE_MIN_INTERVAL_S)
    assert len(wired["csm_calls"]) == calls
    assert (await app_catalog.common_sense_entry(wired["redis"], "com.roblox.client"))["status"] == "checking"


async def test_common_sense_switched_off_asks_nothing_and_says_so(wired):
    await _found_listing(wired)
    await _step(wired)
    assert wired["csm_calls"] == []
    entry = await app_catalog.common_sense_entry(wired["redis"], "com.roblox.client")
    assert entry["status"] == "off" and "Add by address > Cloud accounts" in entry["reason"]


# --- MalwareBazaar ------------------------------------------------------------

SHA = "ab" * 32


def _hashed_inventory(*packages, sha=SHA):
    body = _inventory(*packages)
    for row in body["apps"]:
        row.update(apk_sha256=sha, apk_hash_state="ok")
    return body


async def test_without_a_key_the_check_says_it_is_not_set_up_and_asks_nothing(wired):
    client = wired["client"]
    client.post("/api/telemetry/apps/inventory", headers=HEADERS, json=_hashed_inventory("com.example.tool"))
    await _step(wired)
    data = client.get("/api/telemetry/apps/kid", headers=HEADERS).json()
    check = data["apps"][0]["malware_check"]
    assert check["state"] == "not_set_up" and "abuse.ch MalwareBazaar integration" in check["reason"]
    assert data["lookups"]["malware"]["state"] == "not_set_up"
    assert wired["mb_calls"] == []


async def test_a_known_sample_is_severe_and_every_parent_hears(wired, caplog):
    wired["checks"]["settings"] = {"malwarebazaar_key": "made-up-secret-key"}
    wired["mb"][SHA] = (200, {"query_status": "ok", "data": [{"sha256_hash": SHA, "signature": "ExampleFamily"}]})
    wired["pages"]["com.example.tool"] = (404, "")
    await app_catalog.save_inventory(wired["redis"], "kid", "phone-key-0000000001", _hashed_inventory("com.example.tool"))
    for _ in range(3):
        await _step(wired)
    assert wired["mb_calls"] == [(SHA, "made-up-secret-key")]
    malware = [n for n in wired["sent"] if "malware" in n["body"]]
    assert sorted(n["user"] for n in malware) == ["dad", "mom"]  # severe reaches the severe-only parent too
    assert malware[0]["priority"] == 4
    data = wired["client"].get("/api/telemetry/apps/kid", headers=HEADERS).json()
    assert data["apps"][0]["malware_check"]["state"] == "malware"
    assert "made-up-secret-key" not in json.dumps(data) and "made-up-secret-key" not in caplog.text


async def test_an_unknown_hash_is_not_known_and_is_checked_again_after_the_ttl(wired):
    wired["checks"]["settings"] = {"malwarebazaar_key": "made-up-secret-key"}
    await app_catalog.save_inventory(wired["redis"], "kid", "phone-key-0000000001", _hashed_inventory("com.example.tool"))
    await _step(wired)
    apps, _ = await app_catalog.merged_apps(wired["redis"], "kid")
    assert apps[0]["malware_check"]["state"] == "not_known"
    assert await app_catalog.queue_hashes(wired["redis"], [SHA]) == []
    wired["clock"]["now"] += app_catalog.MALWAREBAZAAR_TTL_DAYS * 86400
    assert await app_catalog.queue_hashes(wired["redis"], [SHA]) == [SHA]


async def test_a_refused_key_is_an_error_that_a_new_key_clears(wired, caplog):
    wired["checks"]["settings"] = {"malwarebazaar_key": "made-up-wrong-key"}
    wired["mb"][SHA] = (401, None)
    await app_catalog.save_inventory(wired["redis"], "kid", "phone-key-0000000001", _hashed_inventory("com.example.tool"))
    await _step(wired)
    apps, _ = await app_catalog.merged_apps(wired["redis"], "kid")
    assert apps[0]["malware_check"]["state"] == "error" and "Auth-Key" in apps[0]["malware_check"]["reason"]
    assert await app_catalog.backoff_state(wired["redis"], app_catalog.MB_BACKOFF_KEY)
    assert "made-up-wrong-key" not in caplog.text
    # A new key lifts the pause and the hash is asked again.
    wired["checks"]["settings"] = {"malwarebazaar_key": "made-up-right-key"}
    del wired["mb"][SHA]
    await _step(wired)
    assert wired["mb_calls"][-1] == (SHA, "made-up-right-key")
    apps, _ = await app_catalog.merged_apps(wired["redis"], "kid")
    assert apps[0]["malware_check"]["state"] == "not_known"


async def test_a_failed_lookup_backs_off_without_naming_the_key(wired, caplog):
    wired["checks"]["settings"] = {"malwarebazaar_key": "made-up-secret-key"}
    wired["mb"][SHA] = OSError("connection reset")
    await app_catalog.save_inventory(wired["redis"], "kid", "phone-key-0000000001", _hashed_inventory("com.example.tool"))
    await _step(wired)
    result = json.loads(await wired["redis"].get(f"{app_catalog.MB_PREFIX}{SHA}"))
    assert result["status"] == "error" and result["reason"] == "OSError"
    assert await app_catalog.backoff_state(wired["redis"], app_catalog.MB_BACKOFF_KEY)
    assert await wired["redis"].zrangebyscore(app_catalog.MB_DUE, 0, wired["clock"]["now"]) == [SHA]
    assert "made-up-secret-key" not in caplog.text


async def test_an_app_the_device_could_not_hash_says_why(wired):
    body = _inventory("com.example.big", "com.example.old")
    body["apps"][0].update(apk_sha256=None, apk_hash_state="too_large")
    await app_catalog.save_inventory(wired["redis"], "kid", "phone-key-0000000001", body)
    apps = {a["package"]: a for a in (await app_catalog.merged_apps(wired["redis"], "kid"))[0]}
    assert apps["com.example.big"]["malware_check"] == {
        "state": "skipped", "reason": "The app file is too large to fingerprint on the device"}
    assert apps["com.example.old"]["malware_check"]["state"] == "no_hash"


async def test_settings_that_cannot_be_read_ask_nothing(wired):
    wired["checks"]["settings"] = RuntimeError("identity down")
    await app_catalog.save_inventory(wired["redis"], "kid", "phone-key-0000000001", _hashed_inventory("com.example.tool"))
    await _step(wired)
    assert wired["mb_calls"] == [] and wired["csm_calls"] == []


# --- Jarvis is never checked --------------------------------------------------


async def test_jarvis_is_never_looked_up_anywhere(wired):
    wired["checks"]["settings"] = {"malwarebazaar_key": "made-up-secret-key",
                                   "common_sense": True}
    await app_catalog.save_inventory(wired["redis"], "kid", "phone-key-0000000001",
                                     _hashed_inventory(app_catalog.JARVIS_ANDROID_PACKAGE, sha="cd" * 32))
    assert await app_catalog.queue_lookups(wired["redis"], [app_catalog.JARVIS_ANDROID_PACKAGE]) == []
    assert await app_catalog.queue_common_sense(wired["redis"], app_catalog.JARVIS_ANDROID_PACKAGE, "Jarvis") is False
    for _ in range(3):
        await _step(wired)
    assert wired["fetched"] == [] and wired["mb_calls"] == [] and wired["csm_calls"] == []
    assert (await app_catalog.merged_apps(wired["redis"], "kid"))[0] == []
