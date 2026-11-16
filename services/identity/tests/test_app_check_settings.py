"""The app checks' global settings: seeded with a description naming where
they are edited, and the MalwareBazaar key treated as a secret."""
import os

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.models import DEFAULT_GLOBAL_SETTINGS, GlobalSetting
from services.identity.schemas import GlobalSettingUpdate

KEY = "malwarebazaar_auth_key"
SECRET = "made-up-abuse-ch-key"


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(GlobalSetting(key=KEY, value=SECRET, description="d"))
        s.add(GlobalSetting(key="app_vetting_common_sense_enabled", value="true", description="d"))
        s.commit()
        yield s


def test_both_settings_are_seeded_with_where_to_edit_them():
    seeded = {s["key"]: s for s in DEFAULT_GLOBAL_SETTINGS}
    assert seeded[KEY]["value"] == ""
    assert seeded["app_vetting_common_sense_enabled"]["value"] == "true"
    for key in (KEY, "app_vetting_common_sense_enabled"):
        assert "Admin > Users & Devices > App checks" in seeded[key]["description"]
        assert "\u2014" not in seeded[key]["description"] and "\u2013" not in seeded[key]["description"]


def test_the_key_is_masked_for_the_ui_and_whole_for_services(session):
    listed = {s.key: s.value for s in main.get_settings(session=session, x_internal_secret=None, auth=True)}
    assert listed[KEY] == "***"
    session.expire_all()
    one = main.get_setting(KEY, session=session, auth=True, x_internal_secret=None)
    assert one.value == "***"
    session.expire_all()
    internal = os.environ["INTERNAL_SECRET"]
    listed = {s.key: s.value for s in main.get_settings(session=session, x_internal_secret=internal, auth=True)}
    assert listed[KEY] == SECRET


def test_saving_the_mask_back_keeps_the_secret(session):
    answer = main.update_setting(KEY, GlobalSettingUpdate(value="***"), session=session, auth=True)
    assert answer.value == "***"
    main.update_settings_bulk({KEY: "***"}, session=session, auth=True)
    session.expire_all()
    stored = session.exec(select(GlobalSetting).where(GlobalSetting.key == KEY)).first()
    assert stored.value == SECRET


def test_a_new_key_is_stored_and_never_echoed(session):
    answer = main.update_setting(KEY, GlobalSettingUpdate(value="another-made-up-key"), session=session, auth=True)
    assert answer.value == "***"
    session.expire_all()
    assert session.exec(select(GlobalSetting).where(GlobalSetting.key == KEY)).first().value == "another-made-up-key"
    cleared = main.update_setting(KEY, GlobalSettingUpdate(value=""), session=session, auth=True)
    assert cleared.value == ""


def test_the_mask_is_refused_for_a_secret_that_was_never_set():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s, pytest.raises(HTTPException) as err:
        main.update_setting(KEY, GlobalSettingUpdate(value="***"), session=s, auth=True)
    assert err.value.status_code == 400
