"""Live recipe OCR: the real vision model reading the family's own photos.

These run the *configured* vision model against the two pictures kept in
``test/fixtures/recipes`` - a handwritten recipe card and a nutrition label.
They are marked ``local_only`` so CI (which has no model) skips them, and each
one skips itself when the vision model is not configured or cannot be reached,
so a laptop without the model still passes.

The endpoint comes from the environment, so the same model the app uses (the
Alpaca router's OpenAI-compatible API) can be pointed at directly:

    VISION_OCR_URL   - the LLM base URL (falls back to OLLAMA_URL)
    VISION_OCR_MODEL - the vision model name (default: qwen2.5-vl:7b)

Run them with the model up (``--run-local`` is what un-skips ``local_only``):

    VISION_OCR_URL=http://192.168.2.43:11434 \\
        .venv/bin/python -m pytest test/local/test_recipe_ocr_live.py -m local_only --run-local -v
"""
import os
from pathlib import Path

import pytest

from services.common import nutrition_label, recipe_ocr
from services.execution.handlers import vision_ocr

FIXTURES = Path(__file__).resolve().parents[2] / "test" / "fixtures" / "recipes"
CARD = FIXTURES / "sugar-cookies-card.png"
LABEL = FIXTURES / "food-label.webp"

DEFAULT_MODEL = "qwen2.5-vl:7b"


def _endpoint() -> tuple[str | None, str]:
    """The LLM base URL and vision model the app would call, from the environment."""
    url = os.getenv("VISION_OCR_URL") or os.getenv("OLLAMA_URL")
    model = os.getenv("VISION_OCR_MODEL") or DEFAULT_MODEL
    return (url.rstrip("/") if url else None), model


async def _read(path: Path, task: str) -> dict:
    """Run the configured vision model on ``path``, skipping when it is not there."""
    proxy_url, model = _endpoint()
    if not proxy_url:
        pytest.skip("no vision endpoint configured (set VISION_OCR_URL or OLLAMA_URL)")
    try:
        result = await vision_ocr.vision_ocr_screenshot(
            str(path), proxy_url=proxy_url, model=model, task=task
        )
    except Exception as exc:  # no model, no network, or an unreadable picture
        pytest.skip(f"vision model unavailable at {proxy_url}: {exc}")
    if result.get("_error"):
        pytest.skip(f"vision model not configured: {result['_error']}")
    return result


@pytest.mark.local_only
@pytest.mark.asyncio
async def test_live_reads_the_handwritten_sugar_cookie_card():
    """The handwritten card is read into a draft with a title and ingredients."""
    result = await _read(CARD, "recipe")
    assert result.get("full_text"), "the model returned no text for the card"

    draft = recipe_ocr.parse(result)
    assert draft["recipe"]["title"], f"no title read; raw text: {draft['raw_text'][:200]!r}"
    joined = " ".join(draft["recipe"]["ingredients"]).lower()
    assert any(
        word in joined for word in ("sugar", "shortening", "egg", "flour")
    ), f"no expected ingredient read; got: {draft['recipe']['ingredients']!r}"


@pytest.mark.local_only
@pytest.mark.asyncio
async def test_live_offers_readings_for_the_words_it_flagged():
    """Whatever the model could not read comes back with a reading to accept."""
    result = await _read(CARD, "recipe")
    draft = recipe_ocr.parse(result)
    flagged = draft["uncertain"]
    if not flagged:
        pytest.skip("the model read the card cleanly this time")

    # Every flagged word has a place to accept a reading.
    assert set(flagged) <= set(draft["suggestions"])
    # The model's own readings (when it gave any) come through unaltered.
    model_readings = result.get("suggestions") or {}
    for phrase in flagged:
        if phrase in model_readings:
            assert draft["suggestions"][phrase] == model_readings[phrase][:3]


@pytest.mark.local_only
@pytest.mark.asyncio
async def test_live_reads_the_nutrition_label():
    """The label photo is read into the printed numbers, not a guess."""
    result = await _read(LABEL, "document")
    text = result.get("full_text", "")
    assert text, "the model returned no text for the label"

    label = nutrition_label.parse(text)
    assert label["draft"]["calories"] is not None, f"no calories read; raw text: {text[:200]!r}"
    # Calories, protein, carbs and fat are what make an entry usable.
    assert not [field for field in nutrition_label.REQUIRED if field in label["missing"]]
