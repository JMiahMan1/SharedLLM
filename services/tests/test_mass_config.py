"""Tests for Music Assistant config_entry_id configuration."""


def test_mass_config_entry_id_from_env(monkeypatch):
    monkeypatch.setenv("MASS_CONFIG_ENTRY_ID", "test-config-123")
    # Force reload to pick up the new env var
    import importlib
    import config
    importlib.reload(config)
    from config import CONFIG
    assert CONFIG.get("mass_config_entry_id") == "test-config-123"


def test_mass_config_entry_id_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("MASS_CONFIG_ENTRY_ID", raising=False)
    import importlib
    import config
    importlib.reload(config)
    from config import CONFIG
    assert CONFIG.get("mass_config_entry_id") == ""
