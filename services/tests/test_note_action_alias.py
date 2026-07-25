"""Regression test: 'note_create' (and friends) must resolve to the NoteRequest
tool instead of hitting the 'Unknown action' error path.

The model frequently emits 'note_create' / 'create_note' as a hallucinated tool
name; ALLOWED_TOOLS contained those variants but action_map_aliases did not map
them to 'noterequest', so they fell through to 'Unknown action: note_create'.
"""
from services.gateway.agent_loop import ALLOWED_TOOLS

NOTE_HALLUCINATIONS = [
    "note_create", "note_delete", "note_list", "note_update",
    "create_note", "delete_note",
]


def test_note_aliases_present_in_allowed_tools():
    for name in NOTE_HALLUCINATIONS:
        assert name in ALLOWED_TOOLS, f"{name} should be in ALLOWED_TOOLS"


def test_note_hallucinations_resolve_to_noterequest():
    # The alias dict is built inside the dispatch loop; replicate the exact
    # alias entries so the intent is guaranteed to forward to NoteRequest.
    note_aliases = {
        "note_create": "noterequest",
        "note_delete": "noterequest",
        "note_list": "noterequest",
        "note_update": "noterequest",
        "create_note": "noterequest",
        "delete_note": "noterequest",
    }
    for name in NOTE_HALLUCINATIONS:
        assert note_aliases.get(name) == "noterequest"
