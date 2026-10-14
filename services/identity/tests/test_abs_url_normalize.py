"""A pasted Audiobookshelf URL is saved as the server root."""
import pytest

from services.identity.main import normalize_abs_url


@pytest.mark.parametrize(
    "given,saved",
    [
        ("https://abs.sumemail.com", "https://abs.sumemail.com"),
        ("https://abs.sumemail.com/", "https://abs.sumemail.com"),
        ("https://abs.sumemail.com/api", "https://abs.sumemail.com"),
        ("https://abs.sumemail.com/api/", "https://abs.sumemail.com"),
        ("https://abs.sumemail.com/login", "https://abs.sumemail.com"),
        (" https://host/audiobookshelf/login ", "https://host/audiobookshelf"),
        ("https://host/audiobookshelf", "https://host/audiobookshelf"),
        ("", ""),
        (None, None),
    ],
)
def test_the_server_root_is_kept(given, saved):
    assert normalize_abs_url(given) == saved
