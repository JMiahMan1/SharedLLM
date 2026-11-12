"""A pasted Audiobookshelf URL is saved as the server root."""
import pytest

from services.identity.main import normalize_abs_url


@pytest.mark.parametrize(
    "given,saved",
    [
        ("https://abs.example.com", "https://abs.example.com"),
        ("https://abs.example.com/", "https://abs.example.com"),
        ("https://abs.example.com/api", "https://abs.example.com"),
        ("https://abs.example.com/api/", "https://abs.example.com"),
        ("https://abs.example.com/login", "https://abs.example.com"),
        (" https://host/audiobookshelf/login ", "https://host/audiobookshelf"),
        ("https://host/audiobookshelf", "https://host/audiobookshelf"),
        ("", ""),
        (None, None),
    ],
)
def test_the_server_root_is_kept(given, saved):
    assert normalize_abs_url(given) == saved
