import pytest


@pytest.fixture(autouse=True)
def _fresh_provider_cache():
    """build_provider keeps providers per login; tests that swap a fake client
    onto the one they built must not hand it to the next test."""
    from services.storage import providers

    providers._PROVIDERS.clear()
    yield
    providers._PROVIDERS.clear()
