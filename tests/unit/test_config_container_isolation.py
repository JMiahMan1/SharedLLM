"""conftest's _isolate_config_modules undoes in-place edits of config containers.

The tests run in file order: the first edits services.config.CONFIG (and a
nested value) in place, the second checks the next test sees the original.
"""
from services import config

_KEY = "__isolation_probe__"
_seen: dict = {}


def test_a_test_may_edit_config_in_place():
    _seen["CONFIG"] = config.CONFIG
    _seen["original_keys"] = set(config.CONFIG)
    nested = next((k for k, v in config.CONFIG.items() if isinstance(v, (dict, list))), None)
    _seen["nested"] = nested
    if nested is not None:
        _seen["nested_value"] = repr(config.CONFIG[nested])
        if isinstance(config.CONFIG[nested], dict):
            config.CONFIG[nested][_KEY] = 1
        else:
            config.CONFIG[nested].append(_KEY)
    config.CONFIG[_KEY] = "leaked"
    assert config.CONFIG[_KEY] == "leaked"


def test_the_next_test_sees_the_original_config():
    assert _seen, "run with the previous test"
    # Same object (modules that imported CONFIG by name hold this one) ...
    assert config.CONFIG is _seen["CONFIG"]
    # ... with its original contents.
    assert _KEY not in config.CONFIG
    assert set(config.CONFIG) == _seen["original_keys"]
    if _seen["nested"] is not None:
        assert repr(config.CONFIG[_seen["nested"]]) == _seen["nested_value"]
