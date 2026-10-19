from services.execution.handlers import timer


def test_emoji_options_have_the_shape_the_ui_expects():
    options = timer.emoji_options()

    assert len(options) == len(timer.EMOJI_SOUNDS)
    assert {"emoji": "🥚", "label": "Egg Timer"} in options
    assert all(set(option) == {"emoji", "label"} for option in options)
    assert all(option["label"] for option in options)


def test_a_missing_label_falls_back_to_the_emoji(monkeypatch):
    monkeypatch.setitem(timer.EMOJI_SOUNDS, "🧪", {})

    assert {"emoji": "🧪", "label": "🧪"} in timer.emoji_options()
