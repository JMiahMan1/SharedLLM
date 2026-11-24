import pytest


@pytest.fixture(autouse=True)
def _fresh_provider_cache():
    """build_provider keeps providers per login; tests that swap a fake client
    onto the one they built must not hand it to the next test."""
    from services.storage import book_cache, providers
    from services.storage.providers_impl import calibre

    providers._PROVIDERS.clear()
    calibre._CATALOGS.clear()
    book_cache.BOOKS.clear()
    yield
    providers._PROVIDERS.clear()
    calibre._CATALOGS.clear()
    book_cache.BOOKS.clear()
