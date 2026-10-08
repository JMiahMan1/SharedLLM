"""A user's Talk, notes and calendar never borrow the Admin account.

Observed live: Michele has no Nextcloud login, so every gap in her context was
filled from NEXTCLOUD_USER -- the Admin account -- and her chat messages were
posted as Admin.
"""

import pytest

from services.execution import nextcloud_client
from services.execution.handlers.talk import handle_talk
from services.execution.personal_data import resolve_personal_data_provider
from services.execution.schemas import TalkRequest, UserContext


@pytest.fixture(autouse=True)
def admin_env(monkeypatch):
    monkeypatch.setattr(nextcloud_client, "NEXTCLOUD_URL", "https://cloud.example")
    monkeypatch.setattr(nextcloud_client, "NEXTCLOUD_USER", "summers")
    monkeypatch.setattr(nextcloud_client, "NEXTCLOUD_PASS", "admin-secret")


def test_a_user_without_a_login_gets_no_account_rather_than_admins():
    assert resolve_personal_data_provider(UserContext(user="michele")) is None


def test_a_half_configured_user_is_not_completed_from_the_admin_account():
    ctx = UserContext(user="michele", nextcloud_user="michele")
    assert resolve_personal_data_provider(ctx) is None


def test_a_user_with_a_login_acts_as_themselves_on_the_shared_server():
    ctx = UserContext(user="jeremiah", nextcloud_user="jeremiah", nextcloud_pass="pw")
    provider = resolve_personal_data_provider(ctx)
    assert provider.username == "jeremiah"
    assert provider.base_url == "https://cloud.example"


def test_a_dict_context_is_read_the_same_way():
    assert resolve_personal_data_provider({"user": "michele"}) is None


def test_a_system_job_with_no_user_keeps_the_server_account():
    provider = resolve_personal_data_provider(UserContext(user=""))
    assert provider.username == "summers"


@pytest.mark.asyncio
async def test_talk_says_whose_account_is_missing():
    result = await handle_talk(TalkRequest(action="send", token="room", message="hi", user_context=UserContext(user="michele")))
    assert result.status == "FAILURE"
    assert "No Nextcloud account is set up for michele" in result.message
