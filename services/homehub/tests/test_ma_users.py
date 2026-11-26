"""The household's Music Assistant accounts follow Identity, with nobody named in code."""

from __future__ import annotations

import pytest

from services.homehub import ma_users

FALLBACK = "audiobookshelf--house"
ALEX = "audiobookshelf--alex"


class FakeDriver:
    platform = "music_assistant"

    def __init__(self, users=None, providers=None, fail=None):
        self.users = users or []
        self.providers = providers or [
            {"domain": "audiobookshelf", "instance_id": ALEX, "name": "Audiobookshelf (alex)"},
            {"domain": "audiobookshelf", "instance_id": FALLBACK, "name": "Audiobookshelf (house)"},
            {"domain": "filesystem_local", "instance_id": "filesystem_local--1", "name": "Music"},
        ]
        self.fail = fail or set()
        self.calls: list[tuple[str, dict]] = []

    async def cmd(self, command, **args):
        self.calls.append((command, args))
        if command in self.fail:
            raise RuntimeError("nope")
        if command == "config/providers":
            return self.providers
        if command == "auth/users":
            return self.users
        return None


class FakeHub:
    def __init__(self, driver):
        self.drivers = {"entry": driver} if driver else {}


def test_an_instance_says_who_it_belongs_to():
    assert ma_users.instance_owner("Audiobookshelf (alex)") == "alex"
    assert ma_users.instance_owner("Audiobookshelf (House Hold)") == "house hold"
    assert ma_users.instance_owner("Audiobookshelf") is None
    assert ma_users.instance_owner(None) is None


def test_a_member_with_an_instance_gets_it_and_everyone_else_the_household_one():
    own, fallback = ma_users.choose_instances(
        [
            {"domain": "audiobookshelf", "instance_id": ALEX, "name": "Audiobookshelf (alex)"},
            {"domain": "audiobookshelf", "instance_id": FALLBACK, "name": "Audiobookshelf (house)"},
        ],
        ["alex", "sam"],
    )
    assert own == {"alex": ALEX}
    assert fallback == FALLBACK


@pytest.mark.asyncio
async def test_a_new_member_gets_an_account_of_their_own_and_the_right_view():
    driver = FakeDriver()
    result = await ma_users.reconcile(FakeHub(driver), ["alex", "sam"], password="temp-pass",
                                      display_names={"alex": "Alex"})

    assert result["created"] == ["alex", "sam"]
    made = {call[1]["username"]: call[1] for call in driver.calls if call[0] == "auth/user/create"}
    assert made["alex"]["provider_filter"] == [ALEX]
    # No instance of their own: the household one, which is the agreed fallback.
    assert made["sam"]["provider_filter"] == [FALLBACK]
    assert made["alex"]["display_name"] == "Alex"
    assert made["sam"]["role"] == "user"


@pytest.mark.asyncio
async def test_an_existing_account_keeps_its_other_sources_and_gains_theirs():
    driver = FakeDriver(users=[{"user_id": "u1", "username": "alex",
                                "provider_filter": ["filesystem_local--1", FALLBACK]}])
    result = await ma_users.reconcile(FakeHub(driver), ["alex"])

    assert result["updated"] == ["alex"]
    update = next(call[1] for call in driver.calls if call[0] == "auth/user/update")
    assert update["user_id"] == "u1"
    assert update["provider_filter"] == ["filesystem_local--1", ALEX]


@pytest.mark.asyncio
async def test_an_account_already_pointed_at_its_own_instance_is_left_alone():
    driver = FakeDriver(users=[{"user_id": "u1", "username": "alex", "provider_filter": [ALEX]}])
    result = await ma_users.reconcile(FakeHub(driver), ["alex"])

    assert result["updated"] == []
    assert not [call for call in driver.calls if call[0] == "auth/user/update"]


@pytest.mark.asyncio
async def test_a_short_password_is_reported_rather_than_sent_to_music_assistant():
    driver = FakeDriver()
    result = await ma_users.reconcile(FakeHub(driver), ["alex"], password="short")

    assert result["created"] == []
    assert result["skipped"][0]["username"] == "alex"
    assert "8 characters" in result["skipped"][0]["reason"]
    assert not [call for call in driver.calls if call[0] == "auth/user/create"]


@pytest.mark.asyncio
async def test_music_assistant_being_off_says_so_and_names_nobody():
    result = await ma_users.reconcile(FakeHub(None), ["alex"])

    assert result["created"] == []
    assert result["skipped"] == [{"username": "alex", "reason": "Music Assistant is not set up"}]


@pytest.mark.asyncio
async def test_one_refusal_does_not_stop_the_others():
    driver = FakeDriver(fail={"auth/user/create"})
    result = await ma_users.reconcile(FakeHub(driver), ["alex", "sam"], password="temp-pass")

    assert result["created"] == []
    assert [entry["username"] for entry in result["skipped"]] == ["alex", "sam"]
    assert "could not create" in result["skipped"][0]["reason"]


async def test_a_member_who_already_has_a_view_is_not_given_a_second_one():
    """An existing Audiobookshelf view stands; it is never joined by another.

    The fallback is whatever instance nobody else claimed, so a member who
    already inherits the household view must not also be handed someone else's
    instance just because that instance was unused by the people in this call.
    """
    driver = FakeDriver(
        users=[
            {
                "user_id": "u1",
                "username": "sam",
                "provider_filter": ["filesystem_local--1", FALLBACK],
            }
        ],
        providers=[
            {"domain": "audiobookshelf", "instance_id": FALLBACK, "name": "Audiobookshelf (house)"},
            {"domain": "audiobookshelf", "instance_id": ALEX, "name": "Audiobookshelf (robin)"},
        ],
    )
    result = await ma_users.reconcile(FakeHub(driver), ["sam"], password="longenough")

    assert result["updated"] == []
    assert [call for call in driver.calls if call[0] == "auth/user/update"] == []
