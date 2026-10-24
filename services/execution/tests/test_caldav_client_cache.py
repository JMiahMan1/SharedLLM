"""The calendar client is kept per exact login, so Nextcloud is not asked to
check the password again on every calendar view."""

from services.execution import personal_data
from services.execution.personal_data import NextcloudPersonalDataProvider


def _provider(user="dad", password="pw"):
    return NextcloudPersonalDataProvider(base_url="https://cloud.test", username=user, password=password)


def test_the_same_login_gets_the_same_client(monkeypatch):
    monkeypatch.setattr(personal_data, "_CALDAV_CLIENTS", {})
    assert _provider().calendar_client() is _provider().calendar_client()


def test_another_login_or_a_new_password_gets_its_own_client(monkeypatch):
    monkeypatch.setattr(personal_data, "_CALDAV_CLIENTS", {})
    dad = _provider().calendar_client()
    assert _provider("kiddo").calendar_client() is not dad
    assert _provider(password="changed").calendar_client() is not dad


def test_a_kept_client_is_replaced_after_a_while(monkeypatch):
    monkeypatch.setattr(personal_data, "_CALDAV_CLIENTS", {})
    first = _provider().calendar_client()
    key = next(iter(personal_data._CALDAV_CLIENTS))
    personal_data._CALDAV_CLIENTS[key] = (first, 0.0)
    assert _provider().calendar_client() is not first
