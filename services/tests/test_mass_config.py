"""Tests for Music Assistant config_entry_id configuration."""
import importlib.util
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.py"


def _fresh_config():
    """services/config.py executed into a private module against the current
    environment. ``importlib.reload(services.config)`` would re-run the shared
    module in place and leave this test's environment in it for every later
    test (and replace the CONFIG dict other modules already hold)."""
    spec = importlib.util.spec_from_file_location("_mass_config_probe", _CONFIG_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mass_config_entry_id_from_env(monkeypatch):
    monkeypatch.setenv("MASS_CONFIG_ENTRY_ID", "test-config-123")
    assert _fresh_config().CONFIG.get("mass_config_entry_id") == "test-config-123"


def test_mass_config_entry_id_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("MASS_CONFIG_ENTRY_ID", raising=False)
    assert _fresh_config().CONFIG.get("mass_config_entry_id") == ""
