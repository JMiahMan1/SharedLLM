"""A RAG search must be told which collection it is allowed to ask for.

`ContextSearchRequest.collection_name` defaults to `system_capabilities`. A model
that omits it therefore searches the CLI toolchain inventory, gets nothing back,
and reports that no results were found -- while the answer it wanted sat in
another collection the tool description never named.

Both ends of that failure are pinned here: the schema field description has to
name the real collections, and the tool's payload hint has to name fields the
schema actually has.
"""
from services.gateway.schemas import ContextSearchRequest
from services.gateway.tool_registry import _RAVEN_TOOL_TABLE

_HINT = next(row for row in _RAVEN_TOOL_TABLE if row[0] == "ContextSearchRequest")
_FIELDS = set(ContextSearchRequest.model_fields)
REAL_COLLECTIONS = {
    "calibre_files",
    "nextcloud_files",
    "ha_entities",
    "system_capabilities",
    "system_learnings",
    "user_facts",
    "conversation_memory",
    "mission_history",
    "network_topology",
    "telemetry_alerts",
}


def test_the_collection_field_names_the_library():
    """`calibre_files` was absent, so the model could not have asked for books."""
    described = ContextSearchRequest.model_fields["collection_name"].description
    assert "calibre_files" in described
    assert "book library" in described


def test_every_collection_it_names_is_a_real_one():
    """Naming a collection that does not exist is worse than naming none."""
    described = ContextSearchRequest.model_fields["collection_name"].description
    named = {
        token.strip(" ,.")
        for token in described.split("(")[0].split(":")[-1].split(",")
        if "_" in token
    }
    assert named
    assert named <= REAL_COLLECTIONS, f"advertises collections that do not exist: {named - REAL_COLLECTIONS}"


def test_the_description_warns_about_the_default():
    """The default is the trap; the description has to say so."""
    doc = (ContextSearchRequest.__doc__ or "").lower()
    assert "system_capabilities" in doc
    assert "default" in doc


def test_the_payload_hint_names_fields_that_exist():
    """`collection` and `top_k` are not fields; `extra=ignore` dropped them."""
    hint = _HINT[6]
    named = {
        token.strip(" .,")
        for token in hint.replace("payload fields:", "").split(",")
    }
    assert named
    for field in named:
        assert field in _FIELDS, f"{field!r} is advertised but not a real field"


def test_the_hint_mentions_the_library_and_the_default():
    hint = _HINT[5]
    assert "calibre_files" in hint
    assert "system_capabilities" in hint
