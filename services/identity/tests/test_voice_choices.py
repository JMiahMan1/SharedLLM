"""Whose voice Jarvis speaks with, and what the household may control.

* Each person chooses a voice in their own settings (preferred_tts_voice);
  none chosen means Jarvis's own voice, never a hard-coded one.
* A spoken reply uses the recognised speaker's voice, else the one person the
  device is assigned to, else Jarvis's (GET /api/internal/voice-for).
* What a satellite with no recognised speaker may control is an admin setting
  (household_device_access): unlocked devices, or none.
"""
from sqlmodel import Session, select

import services.identity.main as identity_main
from services.identity import hub_devices
from services.identity.models import DeviceAssignment, GlobalSetting, User
from services.identity.tests.test_entity_protection import _lock, _permissions, client_fixture, session_fixture  # noqa: F401


def _hub_device(session: Session, entity_id: str, owner: str | None) -> None:
    """A HomeHub satellite synced into identity and assigned (owner None: shared)."""
    hardware = "mac:" + entity_id.split(".")[1]
    hub_devices.sync(session, [{"hardware_id": hardware, "kind": "voice_satellite", "name": entity_id,
                                "entity_ids": [entity_id]}])
    hub_devices.assign(session, "hub:" + hardware, {"owner_username": owner})


def _voice_for(client, **params):
    resp = client.get("/api/internal/voice-for", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _set(session: Session, key: str, value: str) -> None:
    row = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
    if row:
        row.value = value
    else:
        row = GlobalSetting(key=key, value=value)
    session.add(row)
    session.commit()


def test_a_new_account_has_no_voice_of_its_own(session: Session):
    assert User(username="newbie").preferred_tts_voice is None


def test_the_speakers_own_voice_wins(client, session: Session):
    alice = session.exec(select(User).where(User.username == "alice")).first()
    alice.preferred_tts_voice = "piper:en_US-hfc_male-medium"
    session.add(alice)
    session.commit()
    assert _voice_for(client, speaker="alice", entity_id="assist_satellite.kitchen") == {
        "voice": "piper:en_US-hfc_male-medium", "person": "alice", "why": "speaker"}


def test_a_device_assigned_to_one_person_speaks_with_their_voice(client, session: Session):
    bob = session.exec(select(User).where(User.username == "bob")).first()
    bob.preferred_tts_voice = "bm_fable"
    session.add(bob)
    session.commit()
    _hub_device(session, "assist_satellite.bobs_room", owner="bob")
    # No speaker recognised (or one with no voice of their own): the owner's.
    assert _voice_for(client, entity_id="assist_satellite.bobs_room")["voice"] == "bm_fable"
    assert _voice_for(client, speaker="alice", entity_id="assist_satellite.bobs_room")["why"] == "device"


def test_a_shared_or_unassigned_device_speaks_as_jarvis(client, session: Session):
    alice = session.exec(select(User).where(User.username == "alice")).first()
    bob = session.exec(select(User).where(User.username == "bob")).first()
    alice.preferred_tts_voice, bob.preferred_tts_voice = "af_bella", "bm_fable"
    # A control grant to two people says nothing about whose satellite it is.
    session.add_all([alice, bob,
                     DeviceAssignment(device_id="assist_satellite.living_room", user_id=alice.id),
                     DeviceAssignment(device_id="assist_satellite.living_room", user_id=bob.id)])
    session.commit()
    _hub_device(session, "assist_satellite.living_room", owner=None)
    assert _voice_for(client, entity_id="assist_satellite.living_room") == {"voice": None, "person": None, "why": "jarvis"}
    assert _voice_for(client, entity_id="assist_satellite.nobodys")["why"] == "jarvis"
    assert _voice_for(client)["why"] == "jarvis"


def test_voices_stored_before_anyone_could_choose_are_cleared_once(session: Session):
    alice = session.exec(select(User).where(User.username == "alice")).first()
    alice.preferred_tts_voice = "af_heart"
    session.add(alice)
    session.commit()
    identity_main._migrate_unchosen_voices(session)
    session.refresh(alice)
    assert alice.preferred_tts_voice is None
    # Chosen afterwards, it stays: the clearing runs one time only.
    alice.preferred_tts_voice = "af_heart"
    session.add(alice)
    session.commit()
    identity_main._migrate_unchosen_voices(session)
    session.refresh(alice)
    assert alice.preferred_tts_voice == "af_heart"


def test_the_household_uses_unlocked_devices_or_none_as_the_admin_chose(client, session: Session):
    assert _lock(client, "lock.front_door", permitted=["alice"]).status_code == 200
    assert _permissions(client, "household")["all_unprotected"] is True  # the default
    _set(session, "household_device_access", "none")
    perms = _permissions(client, "household")
    assert perms["all_unprotected"] is False and perms["permitted_entity_ids"] == []
    # A value that is not a choice is refused, not guessed: nothing.
    _set(session, "household_device_access", "everything")
    assert _permissions(client, "household")["all_unprotected"] is False
    _set(session, "household_device_access", "unlocked")
    perms = _permissions(client, "household")
    assert perms["all_unprotected"] is True and "lock.front_door" in perms["protected_entity_ids"]
