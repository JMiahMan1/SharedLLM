"""A RAG search must be told which collection it is allowed to ask for.

`ContextSearchRequest.collection_name` used to default to `system_capabilities`.
A model that omitted it therefore searched the CLI toolchain inventory, got
nothing back, and reported that no results were found -- while the answer it
wanted sat in another collection the tool description never named. The field is
now required, so the three ways that failure could happen are each pinned: the
schema rejects an omitted collection, the description names the real
collections, and the payload hint names fields the schema actually has.

"""
import pytest
from pydantic import ValidationError

from services.gateway import orchestrator
from services.gateway.schemas import ContextSearchRequest, ResolvedCredentials
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


def test_an_omitted_collection_is_refused_rather_than_guessed():
    """The silent default was the whole bug, so it must not come back.

    Observed live on 205: a Librarian holding Macduff passages in its system
    prompt called this tool with only a query, silently searched
    system_capabilities, and answered "no relevant context found".
    """
    assert "collection_name" in ContextSearchRequest.model_fields
    assert ContextSearchRequest.model_fields["collection_name"].is_required()


def test_a_query_with_no_collection_does_not_validate():
    with pytest.raises(ValidationError):
        ContextSearchRequest(query="trusting God in poverty")


def test_a_query_naming_the_library_still_validates():
    ok = ContextSearchRequest(query="Macduff on providence", collection_name="calibre_files")
    assert ok.collection_name == "calibre_files"


def test_the_description_says_the_collection_is_required():
    doc = (ContextSearchRequest.__doc__ or "").lower()
    assert "required" in doc
    assert "system_capabilities" in doc


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


def test_the_hint_says_the_collection_is_required_and_names_the_library():
    hint = _HINT[5]
    assert "calibre_files" in hint
    assert "required" in hint


async def test_the_librarian_tool_path_refuses_a_search_with_no_collection(monkeypatch):
    """The single-turn tool map posts straight to /rag/search, bypassing the schema.

    ``SINGLE_TURN_TOOL_ENDPOINTS`` sends ``contextsearchrequest`` to
    ``/rag/search`` with the model's payload forwarded verbatim, so requiring
    the gateway schema field protects nothing on that path -- the search still
    reaches RAG with no collection and RAG still applies its own
    ``nextcloud_files`` default. Observed live: three consecutive calls all
    reported "No relevant context found" about a book whose passages were
    already in the model's own context. So the refusal has to live at the
    dispatch site, and it must happen *before* anything is sent.
    """
    posted = []

    class _Boom:
        async def __aenter__(self):
            raise AssertionError("an under-specified search must never be sent")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(orchestrator, "shared_http_client", lambda: _Boom(), raising=False)
    monkeypatch.setattr(
        "services.gateway.main.shared_http_client",
        lambda: _Boom(),
        raising=False,
    )

    creds = ResolvedCredentials(user="someone")
    bare = await orchestrator._execute_single_tool(
        "ContextSearchRequest", {"query": "Macduff on poverty"}, "Macduff on poverty", creds
    )
    assert "collection_name is required" in bare

    scoped = await orchestrator._execute_single_tool(
        "ContextSearchRequest",
        {"query": "Macduff on poverty", "collection_name": "calibre_files"},
        "Macduff on poverty",
        creds,
    )
    assert "collection_name is required" not in scoped
