"""An empty string stays a string where the field cannot be None."""
from services.execution.schemas import ActivityRequest, NoteRequest


def test_a_driving_event_with_an_empty_item_is_accepted():
    # The phone posts every driving event with item "" (DrivingMonitor.toBody).
    req = ActivityRequest.model_validate({
        "user_context": {"user": "dad"}, "feature": "driving", "event": "hard_brake", "item": "", "detail": {"at": 1},
    })
    assert req.item == ""


def test_an_empty_optional_field_still_reads_as_none():
    req = NoteRequest.model_validate({"user_context": {"user": "dad"}, "action": "read", "path": ""})
    assert req.path is None
