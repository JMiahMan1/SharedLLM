"""The app checks' old global settings: no longer seeded (they are homehub
integrations now), the MalwareBazaar key still masked while its row waits to
be moved, and an emptied row deleted at startup."""
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


def test_neither_setting_is_seeded_any_more():
    seeded = {s["key"] for s in DEFAULT_GLOBAL_SETTINGS}
    assert KEY not in seeded and "app_vetting_common_sense_enabled" not in seeded


def test_emptied_rows_are_deleted_and_unmoved_ones_kept(session):
    main._drop_retired_settings(session)
    kept = {s.key for s in session.exec(select(GlobalSetting)).all()}
    assert {KEY, "app_vetting_common_sense_enabled"} <= kept  # not moved yet: the hub still has to read them
    for row in session.exec(select(GlobalSetting)).all():
        row.value = ""
        session.add(row)
    session.commit()
    main._drop_retired_settings(session)
    left = {s.key for s in session.exec(select(GlobalSetting)).all()}
    assert KEY not in left and "app_vetting_common_sense_enabled" not in left


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
