"""The assistant's meal-plan write action: plan a dinner from a sentence.

The handler owns no storage: it reads and rewrites ``Recipes/Meal plan.md``
through the ordinary note API. These tests stand a fake note store behind that
API (a dict of files plus the WebDAV verbs the note handler uses) so nothing
touches a live Nextcloud.
"""
from datetime import date
from urllib.parse import unquote

import pytest

from services.execution.handlers import meal_plan
from services.execution.handlers import note as note_handler
from services.execution.schemas import MealPlanRequest

PLAN = "Recipes/Meal plan.md"


class FakeNoteStore:
    """A fake Nextcloud notes store: files in a dict, WebDAV verbs on top."""

    def __init__(self, files: dict[str, str] | None = None, fail_reads: bool = False):
        self.files = dict(files or {})
        self.puts: list[tuple[str, str]] = []
        self.fail_reads = fail_reads

    async def request(self, method, url, data=None, headers=None, **kwargs):
        path = unquote(url.split("nextcloud.test/", 1)[-1])
        if method == "GET":
            if self.fail_reads:
                return {"status_code": 503, "text": "", "headers": {}}
            if path in self.files:
                return {"status_code": 200, "text": self.files[path], "headers": {"ETag": '"1"'}}
            return {"status_code": 404, "text": "", "headers": {}}
        if method == "PUT":
            body = (data or b"").decode("utf-8")
            self.puts.append((path, body))
            self.files[path] = body
            return {"status_code": 204, "text": "", "headers": {"ETag": '"2"'}}
        if method == "PROPFIND":
            prefix = path.rstrip("/") + "/"
            listed = [p for p in self.files if p.startswith(prefix) and p.endswith(".md")]
            return {"status_code": 207, "text": self._multistatus(listed), "headers": {}}
        return {"status_code": 405, "text": "", "headers": {}}

    def _multistatus(self, paths: list[str]) -> str:
        responses = "".join(
            f"<d:response><d:href>/{p}</d:href><d:propstat><d:prop>"
            f"<d:resourcetype/><d:getcontentlength>{len(self.files[p])}</d:getcontentlength>"
            f"<d:getlastmodified>Wed, 01 Oct 2026 12:00:00 GMT</d:getlastmodified>"
            f"</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            for p in paths
        )
        return f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">{responses}</d:multistatus>'


class FakeProvider:
    username = "u"
    password = "p"
    base_url = "http://nextcloud.test"

    def __init__(self):
        self.dirs: list[str] = []

    async def ensure_directory(self, path):
        self.dirs.append(path)

    def file_url(self, path):
        return f"http://nextcloud.test/{path}"

    def sanitize_filename(self, value, fallback):
        return (value or fallback).replace(" ", "_")


@pytest.fixture
def store(monkeypatch):
    """Wire the fake note store behind the note handler and return it."""
    import services.execution.http_client as http_client

    note_store = FakeNoteStore()
    monkeypatch.setattr(http_client, "request", note_store.request)
    monkeypatch.setattr(note_handler, "resolve_personal_data_provider", lambda _ctx: FakeProvider())
    return note_store


def request(**kwargs) -> MealPlanRequest:
    return MealPlanRequest(user_context={"user": "dad"}, **kwargs)


# --- pure helpers -----------------------------------------------------------


def test_a_weekday_in_the_sentence_is_the_plan_slot():
    assert meal_plan.resolve_plan_day("add chicken chili to Tuesday") == "Tue"
    assert meal_plan.resolve_plan_day("plan pizza for Friday") == "Fri"
    assert meal_plan.resolve_plan_day("put it on Thursday") == "Thu"


def test_today_and_tomorrow_follow_the_monday_based_week():
    # 2026-10-28 is a Wednesday.
    today = date(2026, 10, 28)
    assert meal_plan.plan_day_of(today) == "Wed"
    assert meal_plan.resolve_plan_day("add soup tonight", today) == "Wed"
    assert meal_plan.resolve_plan_day("add soup tomorrow", today) == "Thu"
    # Monday-first, so Sunday is the last slot.
    assert meal_plan.plan_day_of(date(2026, 11, 1)) == "Sun"


def test_the_recipe_name_is_stripped_of_the_day_and_filler():
    assert meal_plan.parse_recipe_name("plan pizza for Friday") == "pizza"
    assert meal_plan.parse_recipe_name("add a slow-cooker meal to Tuesday") == "slow-cooker"
    assert meal_plan.parse_recipe_name("put Chicken Chili on the menu for Monday") == "Chicken Chili"


def test_a_spoken_fragment_finds_the_shelf_note():
    titles = ["Beef Stew", "Slow Cooker Chicken Chili", "Slow Cooker Beef Stew", "Pizza"]
    assert meal_plan.match_recipe_title("pizza", titles) == "Pizza"
    assert meal_plan.match_recipe_title("slow cooker beef stew", titles) == "Slow Cooker Beef Stew"
    # An ambiguous fragment picks one deterministically; the reply names it.
    assert meal_plan.match_recipe_title("slow cooker", titles) == "Slow Cooker Beef Stew"
    # A name with no shelf note stays as typed; the plan is not blocked.
    assert meal_plan.match_recipe_title("Mystery Pie", titles) == "Mystery Pie"


def test_apply_meal_replaces_one_line_and_keeps_the_rest():
    body = "#meal-plan\n\n- [x] Mon · Beef Stew\n- [ ] Tue · Pizza\n\nA note to myself."
    updated = meal_plan.apply_meal(body, "Tue", "Chicken Chili")
    assert "- [ ] Tue · Chicken Chili" in updated
    assert "- [x] Mon · Beef Stew" in updated  # the cooked tick survives
    assert "\nA note to myself." in updated  # anything else is untouched
    # A day that is not there yet is inserted in plan order, not appended.
    inserted = meal_plan.apply_meal(body, "Wed", "Tacos")
    assert inserted.splitlines().index("- [ ] Wed · Tacos") == 4


def test_apply_meal_reads_a_hand_written_lowercase_line():
    # The TS parser is case-insensitive, so the handler must be too, or a
    # lowercase day would be planned twice.
    body = "#meal-plan\n\n- [ ] tue · Pizza"
    assert meal_plan.apply_meal(body, "Tue", "Tacos") == "#meal-plan\n\n- [ ] Tue · Tacos"


# --- the handler ------------------------------------------------------------


async def test_plans_a_day_from_the_sentence_and_writes_the_note(store):
    store.files[PLAN] = "# Meal plan\nCategory: Recipes\n\n#meal-plan\n\n- [ ] Mon · Beef Stew"
    result = await meal_plan.handle_meal_plan(request(text="add chicken chili to Tuesday"))

    assert result.status == "SUCCESS"
    assert result.service == "meal_plan"
    assert result.detail == {"day": "Tue", "recipe": "chicken chili", "path": PLAN}
    assert len(store.puts) == 1
    path, body = store.puts[0]
    assert path == PLAN
    assert "- [ ] Tue · chicken chili" in body
    assert "- [ ] Mon · Beef Stew" in body  # the rest of the week is preserved


async def test_uses_the_shelf_notes_own_title(store):
    store.files[PLAN] = "#meal-plan\n\n- [ ] Mon · Beef Stew"
    store.files["Recipes/Slow Cooker Chicken Chili.md"] = "# Slow Cooker Chicken Chili\nCategory: Recipes\n\n#slow-cooker"

    result = await meal_plan.handle_meal_plan(request(text="add a slow-cooker meal to Tuesday"))

    assert result.status == "SUCCESS"
    assert result.detail["recipe"] == "Slow Cooker Chicken Chili"
    assert "- [ ] Tue · Slow Cooker Chicken Chili" in store.puts[0][1]


async def test_an_explicit_day_and_recipe_win(store):
    store.files[PLAN] = "#meal-plan\n\n- [ ] Mon · Beef Stew"
    result = await meal_plan.handle_meal_plan(request(text="plan something nice", day="Friday", recipe="Pizza"))
    assert result.status == "SUCCESS"
    assert result.detail == {"day": "Fri", "recipe": "Pizza", "path": PLAN}
    assert "- [ ] Fri · Pizza" in store.puts[0][1]


async def test_a_missing_note_is_a_first_run_not_a_failure(store):
    result = await meal_plan.handle_meal_plan(request(text="plan pizza for Friday"))
    assert result.status == "SUCCESS"
    assert store.puts[0][1].startswith("# Meal plan\nCategory: Recipes\n\n#meal-plan")
    assert "- [ ] Fri · pizza" in store.puts[0][1]


async def test_a_failed_read_is_reported_and_nothing_is_written(store):
    store.fail_reads = True
    result = await meal_plan.handle_meal_plan(request(text="plan pizza for Friday"))
    assert result.status == "FAILURE"
    assert result.service == "meal_plan"
    # The server's own words, not a generic message.
    assert "read" in result.message.lower() or "HTTP" in result.message
    assert store.puts == []  # never planned over a plan it could not read


async def test_a_sentence_with_no_day_is_refused(store):
    result = await meal_plan.handle_meal_plan(request(text="plan pizza"))
    assert result.status == "FAILURE"
    assert "which day" in result.message.lower()
    assert store.puts == []


async def test_a_sentence_with_no_recipe_is_refused(store):
    result = await meal_plan.handle_meal_plan(request(text="add to Tuesday"))
    assert result.status == "FAILURE"
    assert "what should i plan" in result.message.lower()
    assert store.puts == []


# --- the no-credentials fallback: the week as an ICS feed -------------------


def test_build_plan_ics_publishes_one_event_per_planned_day():
    body = "#meal-plan\n\n- [x] Mon · Chicken Chili\n- [ ] Tue · Pizza\n- [ ] Wed ·\n"
    text, events = meal_plan.build_plan_ics(body, today=date(2026, 10, 28))  # a Wednesday
    assert events == 2
    assert "BEGIN:VCALENDAR" in text and "BEGIN:VEVENT" in text
    assert "Dinner: Chicken Chili" in text and "Dinner: Pizza" in text
    # Monday of that week is 2026-10-26; the events sit on the plan's own dates.
    assert "20261026T180000" in text and "20261027T180000" in text
    # The empty day publishes nothing.
    assert "Wed" not in text


def test_build_plan_ics_uid_is_the_date_so_a_republish_replaces():
    text, _ = meal_plan.build_plan_ics("- [ ] Mon · Chili", today=date(2026, 10, 28))
    assert "meal-plan-2026-10-26@jarvis-os" in text


async def test_plan_ics_reads_the_note_and_returns_the_feed(store):
    store.files[PLAN] = "#meal-plan\n\n- [ ] Mon · Chicken Chili\n- [ ] Fri · Pizza\n"
    result = await meal_plan.handle_meal_plan(request(action="ics"))
    assert result.status == "SUCCESS"
    assert result.service == "meal_plan"
    assert result.detail["events"] == 2
    assert "BEGIN:VCALENDAR" in result.detail["ics"]
    # A read, never a write.
    assert store.puts == []


async def test_plan_ics_names_an_empty_plan(store):
    store.files[PLAN] = "#meal-plan\n\n- [ ] Mon ·\n"
    result = await meal_plan.handle_meal_plan(request(action="ics"))
    assert result.status == "FAILURE"
    assert "No dinners are planned" in result.message
    assert store.puts == []


async def test_plan_ics_reports_a_failed_read(store):
    store.fail_reads = True
    result = await meal_plan.handle_meal_plan(request(action="ics"))
    assert result.status == "FAILURE"
    assert store.puts == []
