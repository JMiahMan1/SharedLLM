import pytest

from math_utils import normalize_username, parse_port


def test_normalize_username_basic():
    assert normalize_username("  Jeremiah Home Desktop  ") == "jeremiah_home_desktop"


def test_parse_port_accepts_numeric_strings():
    assert parse_port("11435") == 11435
    assert parse_port(" 8002 ") == 8002


def test_parse_port_uses_default_for_missing_values():
    assert parse_port("", default=9000) == 9000
    assert parse_port(None, default=9000) == 9000


def test_parse_port_rejects_out_of_range_values():
    with pytest.raises(ValueError):
        parse_port("70000")
