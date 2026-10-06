"""Book text must be asked for, never assumed.

`calibre_files` holds 3,000 books. It is the largest collection in the RAG index
and, for an arbitrary question, very unlikely to hold the answer. If it were part
of the standing collection set it would spend the context budget on every turn
and quietly become more dominant as the library grew -- the same reason
`nextcloud_files` is not in the standing set either.

So it is routed in by keyword, ahead of everything else when it fires, and the
routing decision is pinned here rather than asserted through an HTTP client.
"""
from services.gateway.orchestrator import (
    COLLECTION_HEADERS,
    LIBRARY_INTENT_SIGNALS,
    _collection_header,
    _collections_for_query,
    _library_intent_asked,
)


def test_an_ordinary_question_never_searches_the_library():
    """The important one: silence is the default."""
    assert "calibre_files" not in _collections_for_query("what is the weather tomorrow")
    assert "calibre_files" not in _collections_for_query("turn off the porch light")
    assert "calibre_files" not in _collections_for_query("hello")


def test_a_book_question_searches_the_library():
    assert "calibre_files" in _collections_for_query("what does this book say about prayer?")
    assert "calibre_files" in _collections_for_query("find in my library the passage on faith")
    assert "calibre_files" in _collections_for_query("summarize the commentary on Romans")


def test_the_library_is_searched_first_when_asked_for():
    """A quotation beats anything the other collections hold."""
    collections = _collections_for_query("any commentary on the book of Job")
    assert collections[0] == "calibre_files"


def test_the_library_appears_exactly_once():
    collections = _collections_for_query("book library library books")
    assert collections.count("calibre_files") == 1


def test_a_coding_question_does_not_pull_in_the_library():
    """Coding intent reorders the base set; it must not summon book text."""
    collections = _collections_for_query("fix the failing test in this file")
    assert "calibre_files" not in collections
    assert collections[0] == "system_capabilities"


def test_book_intent_wins_over_coding_intent_when_both_are_present():
    """A hybrid ask still deserves the book, because that is what was named."""
    collections = _collections_for_query("quote the book where it mentions this workspace file")
    assert collections[0] == "calibre_files"


def test_the_base_set_is_unchanged_by_a_plain_question():
    """Guard against the standing set drifting while book routing is added."""
    assert _collections_for_query("is the garage door open") == [
        "ha_entities",
        "nextcloud_files",
        "system_capabilities",
        "system_learnings",
    ]


def test_routing_is_case_insensitive():
    assert _library_intent_asked("THE BOOK OF ROMANS") is True
    assert _library_intent_asked("SERMON on the mount") is True


def test_bare_verbs_do_not_count_as_book_intent():
    """"Read the logs" is a systems task, and "read" cannot be a book signal.

    This is why the signal list is narrower than LIBRARIAN_SIGNALS: that one
    picks the MODEL, so it can afford to be broad. This one spends the retrieval
    budget, so it may not fire on a merely Librarian-ish question.
    """
    for query in ("read the logs", "read the file", "fix the workspace"):
        assert _library_intent_asked(query) is False, query


def test_the_intent_list_omits_the_broad_librarian_words():
    """`notes`, `music` and `storage` route to the Librarian but not to books."""
    for token in ("notes", "music", "storage", "cloud", "files", "code", "scripts"):
        assert token not in LIBRARY_INTENT_SIGNALS, token


def test_the_prompt_header_names_the_library_in_words():
    """`CALIBRE_FILES` is an identifier, not something the model can reason about."""
    assert _collection_header("calibre_files") == COLLECTION_HEADERS["calibre_files"]
    assert "LIBRARY" in _collection_header("calibre_files")
    assert _collection_header("nextcloud_files") == "NEXTCLOUD_FILES"

def test_forcing_the_library_in_beats_the_keyword_gate():
    """``include_library`` is the answer to "what did <surname> say?".

    No keyword list can match an author name, and the library holds 1,347 of
    them. When the caller already knows a library is wanted, the wording of the
    question stops being the gate.
    """
    got = _collections_for_query("what did Macduff say about poverty?", include_library=True)
    assert got[0] == "calibre_files"
    assert got.count("calibre_files") == 1


def test_forcing_the_library_in_does_not_disturb_the_coding_set():
    """A forced library joins whatever set the query already chose."""
    got = _collections_for_query("fix the code in workspace", include_library=True)
    assert got[0] == "calibre_files"
    assert "system_capabilities" in got
    assert got.count("calibre_files") == 1


def test_the_library_header_says_the_passages_are_already_retrieved():
    """Guards a real production failure.

    Asked what Macduff said about trusting God in poverty and trial, the model
    was handed eight correctly-located passages from *Memories of Bethany* and
    ignored them: it called ContextSearchRequest with no `collection_name`,
    which defaults to system_capabilities, got nothing, and answered "no results
    were found" with the evidence unread in its own system prompt.

    The header is the only place that can tell it otherwise.
    """
    header = _collection_header("calibre_files")
    assert "ALREADY RETRIEVED" in header
    assert "Do not call ContextSearchRequest" in header
    assert "cite" in header.lower()


def test_only_the_library_header_carries_the_do_not_research_instruction():
    """The instruction is specific to a collection whose passages are preloaded.

    Applying it everywhere would tell the model to distrust context that was
    not in fact preloaded.
    """
    for other in ("ha_entities", "nextcloud_files", "system_capabilities",
                  "system_learnings"):
        assert "ALREADY RETRIEVED" not in _collection_header(other)
