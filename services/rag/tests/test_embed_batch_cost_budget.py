"""Cost-bounded embed batching, calibrated against the production OOM.

Count-bounded chunking alone did not stop the kills. Live measurements from
the per-batch RSS logs on ``POST /rag/sync/ha`` (697 entities, nomic-embed
v1.5, 6 GiB cgroup):

    batch 1/6 (128 texts, <=178 chars) rss=1818   <- warmup
    batch 2/6 (128) rss=1887   batch 4/6 (128) rss=1899
    batch 3/6 (128) rss=1891   batch 5/6 (128) rss=1905   <- flat
    batch 6/6 (57, carrying two 1,144-char entries) rss=5654   <- +3.7 GiB
    write loop (697 rows) rss=5654   <- zero

ONNX pads a batch to its longest text and attention is quadratic in that
length, so the score to bound is ``count * longest**2``: the safe batches
score 128 * 178**2 = 4.1M while the fatal one scores 57 * 1144**2 = 75M.
``EMBED_CHAR_BUDGET`` sits between them, and these tests pin both sides of
that calibration so the constant cannot drift into the measured-fatal zone
without a test naming the numbers.

A second incident added the ``EMBED_MAX_CHARS`` clip: ``POST
/rag/sync/capabilities`` embeds pretty-printed JSON schemas (tens of KB
each), and the budget's never-drop valve delivered one such text to ONNX as
a batch of one -- holding the live service at 5.08 GiB / 6 GiB and
340-378% CPU long enough to starve the 5-second health checks and time out
every waiting client, all without tripping the cgroup. The embedding view
of an oversized text is now clipped; stored rows and FTS keep full content.
"""

import pytest

from services.rag import main as rag_main


def _budget_batches(texts, **kwargs):
    return list(rag_main._batch_by_cost(texts, **kwargs))


def _run_embedded(texts, monkeypatch, **kwargs):
    seen = []

    def fake(batch):
        seen.append(list(batch))
        return [[0.0] * 4 for _ in batch]

    monkeypatch.setattr(rag_main, "embed", fake)
    vectors = rag_main.embed_batched(texts, **kwargs)
    return seen, vectors


def test_the_budget_admits_the_shape_measured_safe():
    safe = rag_main.EMBED_BATCH_SIZE * 178**2
    fatal = 57 * 1144**2
    assert safe <= rag_main.EMBED_CHAR_BUDGET < fatal, (
        f"budget {rag_main.EMBED_CHAR_BUDGET} must keep the measured-safe "
        f"batch ({safe}) and reject the measured-fatal one ({fatal})"
    )


def test_a_measured_safe_batch_is_not_split():
    texts = ["x" * 178 for _ in range(rag_main.EMBED_BATCH_SIZE)]
    batches = _budget_batches(texts, batch_size=rag_main.EMBED_BATCH_SIZE, char_budget=rag_main.EMBED_CHAR_BUDGET)
    assert [len(b) for b in batches] == [128]


def test_the_fatal_batch_shape_is_split_down():
    texts = ["x" * 1144 for _ in range(57)]
    batches = _budget_batches(texts, batch_size=rag_main.EMBED_BATCH_SIZE, char_budget=rag_main.EMBED_CHAR_BUDGET)
    assert len(batches) > 1, "57 long texts must not travel as one batch"
    for batch in batches:
        score = len(batch) * 1144**2
        assert score <= rag_main.EMBED_CHAR_BUDGET


def test_every_text_is_embedded_once_in_the_order_it_was_given(monkeypatch):
    texts = ["short"] * 40 + ["y" * 1144 for _ in range(9)] + ["z"] * 40
    seen, vectors = _run_embedded(texts, monkeypatch)
    flat = [t for batch in seen for t in batch]
    assert flat == texts
    assert len(vectors) == len(texts)


def test_a_text_longer_than_the_budget_stands_alone_and_is_never_dropped(monkeypatch):
    huge = "h" * 10_000
    texts = ["fine", huge, "fine too"]
    seen, vectors = _run_embedded(texts, monkeypatch)
    flat = [t for batch in seen for t in batch]
    assert huge[: rag_main.EMBED_MAX_CHARS] in flat
    assert len(flat) == len(texts), "the clip must shorten, never drop"
    assert any(flat[1] in batch and len(batch) == 1 for batch in seen)
    assert len(vectors) == len(texts)


def test_every_text_over_the_embed_limit_is_clipped_before_embedding(monkeypatch):
    schema = "Capability: CalibreRequest | " + '{"type": "object"}' * 4000
    assert len(schema) > rag_main.EMBED_MAX_CHARS
    texts = ["short", schema, "another short"]
    seen, vectors = _run_embedded(texts, monkeypatch)
    flat = [t for batch in seen for t in batch]
    assert len(flat) == len(texts)
    assert flat[0] == "short" and flat[2] == "another short"
    assert flat[1] == schema[: rag_main.EMBED_MAX_CHARS]
    assert all(len(t) <= rag_main.EMBED_MAX_CHARS for t in flat)
    assert len(vectors) == len(texts)


def test_texts_at_the_limit_are_left_exactly_as_they_are(monkeypatch):
    exact = "x" * rag_main.EMBED_MAX_CHARS
    seen, _ = _run_embedded([exact], monkeypatch)
    assert seen == [[exact]]


def test_the_clip_is_reported_rather_than_silent(monkeypatch, caplog):
    caplog.set_level("WARNING")
    schema = "y" * (rag_main.EMBED_MAX_CHARS + 1)
    _run_embedded([schema, "z"], monkeypatch)
    line = next(r.message for r in caplog.records if "clipping" in r.message)
    assert "1 text(s)" in line
    assert str(rag_main.EMBED_MAX_CHARS) in line
    assert "stored content is unchanged" in line


def test_no_clip_warning_when_nothing_is_oversized(monkeypatch, caplog):
    caplog.set_level("WARNING")
    _run_embedded(["fine", "also fine"], monkeypatch)
    assert not [r for r in caplog.records if "clipping" in r.message]


def test_an_invalid_embed_limit_fails_loudly(monkeypatch):
    with pytest.raises(ValueError, match="max_chars"):
        rag_main.embed_batched(["x"], max_chars=0)


def test_the_hard_batch_cap_still_applies():
    texts = ["x" * 10 for _ in range(500)]
    batches = _budget_batches(texts, batch_size=16, char_budget=10**12)
    assert [len(b) for b in batches] == [16] * 31 + [4]


def test_an_invalid_budget_fails_loudly_instead_of_disabling_the_ceiling(monkeypatch):
    with pytest.raises(ValueError, match="char_budget"):
        rag_main.embed_batched(["x"], char_budget=0)
    with pytest.raises(ValueError, match="batch_size"):
        rag_main.embed_batched(["x"], batch_size=0)


def test_each_batch_is_logged_with_its_resident_memory(monkeypatch, caplog):
    caplog.set_level("INFO")
    seen, _ = _run_embedded(["a", "b"], monkeypatch)
    assert seen
    line = next(r.message for r in caplog.records if "[embed_batched] batch" in r.message)
    assert "batch 1/1" in line and "rss=" in line


def test_the_budget_helper_is_what_embed_batched_uses():
    import inspect

    source = inspect.getsource(rag_main.embed_batched)
    assert "_batch_by_cost(" in source
    assert "range(0, len(texts), batch_size)" not in source, (
        "slicing on a fixed count is what let one long entry decide the peak"
    )
