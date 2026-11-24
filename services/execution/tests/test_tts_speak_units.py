"""Units after a number are said as words: "72°F" was spoken as "72 F"."""
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("soundfile", MagicMock())

from services.execution.tts import speak_units  # noqa: E402 - soundfile must be mocked first


@pytest.mark.parametrize(("raw", "spoken"), [
    ("It is 72°F outside.", "It is 72 degrees Fahrenheit outside."),
    ("High of 72 F today", "High of 72 degrees Fahrenheit today"),
    ("High of 72F today", "High of 72 degrees Fahrenheit today"),
    ("72 degrees F", "72 degrees Fahrenheit"),
    ("Low -5°F tonight", "Low minus 5 degrees Fahrenheit tonight"),
    ("It is 20°C", "It is 20 degrees Celsius"),
    ("98.6°F", "98.6 degrees Fahrenheit"),
    ("turn 90° left", "turn 90 degrees left"),
    ("winds 10 mph", "winds 10 miles per hour"),
    ("30 km/h", "30 kilometers per hour"),
    ("a 40% chance", "a 40 percent chance"),
    ("He is 5'10\" tall", "He is 5 feet 10 inches tall"),
])
def test_units_are_said_as_words(raw, spoken):
    assert speak_units(raw) == spoken


@pytest.mark.parametrize("text", [
    "He got a grade F.",
    "Play it in F sharp.",
    "Fahrenheit 451 is a novel.",
    "Psalm 23 says the Lord is my shepherd.",
    "1 Corinthians 13 is about love.",
    "vitamin C helps",
])
def test_words_that_are_not_units_are_left_alone(text):
    assert speak_units(text) == text


def test_a_range_is_not_a_minus_sign():
    assert speak_units("10-15°F") == "10-15 degrees Fahrenheit"
