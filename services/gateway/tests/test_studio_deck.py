"""A deck and a game are files, so they must render without a browser.

The Markdown is written by a person or a model that will not get reveal.js
syntax right, so the parser is deliberately small and its edge cases are pinned
here: notes, captions, quotes, empty slides, escaping. A game must be one file
that runs with no network, so the template is checked for the sprites hook and for
anything that would fetch.
"""
from __future__ import annotations

from services.gateway.studio import deck, templates


def test_a_deck_splits_on_dividers_and_reads_titles_bullets_notes():
    slides = deck.parse(
        "# The God Who Works\n"
        "- Romans 8:28\n"
        "- Three points\n"
        ":: slow down on point two\n"
        "\n---\n\n"
        "# He works in the waiting\n"
        "![alt](assets/field.png \"A field at dawn\")\n"
        "- Not late, not early\n"
    )

    assert [slide.title for slide in slides] == ["The God Who Works", "He works in the waiting"]
    assert slides[0].bullets == ["Romans 8:28", "Three points"]
    assert slides[0].note == "slow down on point two"
    assert (slides[1].image, slides[1].caption) == ("assets/field.png", "A field at dawn")


def test_quotes_and_plain_lines_still_reach_the_slide():
    slides = deck.parse("# One\n> A quotation\nA plain sentence")

    assert slides[0].bullets == ["A quotation", "A plain sentence"]


def test_empty_slides_are_dropped():
    assert [slide.title for slide in deck.parse("# A\n\n---\n\n---\n\n# B")] == ["A", "B"]


def test_rendering_produces_one_self_contained_document():
    page = deck.render(deck.parse("# Hi\n- there"), "My Deck", footer="Sunday")

    assert page.startswith("<!doctype html>")
    assert "<h1>Hi</h1>" in page and "<li>there</li>" in page
    assert "My Deck" in page and "Sunday" in page
    # No CDN, no remote fonts, no external scripts: it must open offline.
    assert "http://" not in page and "https://" not in page
    assert "__CSS__" not in page and "__SLIDES__" not in page
    assert "ArrowRight" in page, "keyboard navigation is part of the deck"


def test_slide_text_is_escaped():
    page = deck.render(deck.parse("# <script>alert(1)</script>"), "T")

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_the_game_template_is_one_file_with_a_sprites_hook():
    page = templates.render(
        templates.GAME_HTML,
        title="Star Catcher",
        sprites='{"player": "assets/hero.png"}',
        js=templates.GAME_JS,
    )

    assert '<canvas id="game"' in page
    assert 'id="sprites"' in page and "assets/hero.png" in page
    assert "__SPRITES__" not in page and "__JS__" not in page
    assert "http://" not in page and "https://" not in page
    assert "requestAnimationFrame" in page and "touchmove" in page


def test_placeholders_are_replaced_once():
    assert templates.render("__A__ and __B__", a="1", b="2") == "1 and 2"
    assert templates.render("no tokens", a="1") == "no tokens"
