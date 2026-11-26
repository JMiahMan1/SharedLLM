"""What a phone may claim about its own voice turn, and what it may not."""
from services.common import voice_phone


def test_a_signed_in_phone_may_say_where_it_is_and_which_turn_it_is():
    context = voice_phone.app_context(
        {"source": "android", "area": "Office", "area_id": "office", "device_name": "Pixel 9",
         "conversation_id": "c-8f2a", "asked": "what time is it"},
        authenticated=True,
    )
    assert context == {
        "area": "Office",
        "area_id": "office",
        "device_name": "Pixel 9",
        "conversation_id": "c-8f2a",
        "asked": "what time is it",
    }


def test_a_phone_can_never_name_an_echos_satellite_or_speaker():
    """Those decide where a timer rings and where unnamed media plays."""
    context = voice_phone.app_context(
        {"source": "android", "area": "Office",
         "satellite": "assist_satellite.office_echo", "media_player": "media_player.kitchen"},
        authenticated=True,
    )
    assert context == {"area": "Office"}


def test_a_caller_that_is_not_signed_in_gets_nothing():
    assert voice_phone.app_context({"source": "android", "area": "Office"}, authenticated=False) is None


def test_a_context_that_does_not_say_it_is_a_phone_is_not_a_phones():
    """Typed chat, a kiosk and an Echo all send no source, or another one."""
    assert voice_phone.app_context({"area": "Office"}, authenticated=True) is None
    assert voice_phone.app_context({"source": "echo", "area": "Office"}, authenticated=True) is None
    assert voice_phone.app_context(None, authenticated=True) is None


def test_a_conversation_id_that_is_not_one_is_dropped_rather_than_shortened():
    for bad in ("has spaces", "a/b", "", "x" * 65, 12, {"id": 1}):
        context = voice_phone.app_context(
            {"source": "android", "area": "Office", "conversation_id": bad}, authenticated=True)
        assert context == {"area": "Office"}, bad


def test_every_claim_is_bounded_and_blank_claims_are_left_out():
    context = voice_phone.app_context(
        {"source": "android", "area": "  ", "device_name": "D" * 400, "asked": True,
         "extra_system_prompt": "P" * 5000},
        authenticated=True,
    )
    assert "area" not in context and "asked" not in context
    assert context["device_name"] == "D" * 80
    assert context["extra_system_prompt"] == "P" * 1000


def test_a_context_of_nothing_at_all_is_none_so_the_caller_is_not_told_it_worked():
    assert voice_phone.app_context({"source": "android"}, authenticated=True) is None
