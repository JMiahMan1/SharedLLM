from services.common.ha_redirect import redirect, redirects

RAW = "https://ha.sumemail.com=http://192.168.2.205:8123, http://192.168.2.20:8123=http://192.168.2.205:8123/"


def test_redirects_by_origin_whatever_the_path():
    assert redirect("https://ha.sumemail.com", RAW) == "http://192.168.2.205:8123"
    assert redirect("https://ha.sumemail.com/", RAW) == "http://192.168.2.205:8123"
    assert redirect("https://HA.sumemail.com:443", RAW) == "http://192.168.2.205:8123"
    assert redirect("http://192.168.2.20:8123", RAW) == "http://192.168.2.205:8123"


def test_other_urls_and_empty_values_are_untouched():
    assert redirect("http://192.168.2.20:8095", RAW) == "http://192.168.2.20:8095"
    assert redirect("https://other.example", RAW) == "https://other.example"
    assert redirect(None, RAW) is None
    assert redirect("", RAW) == ""
    assert redirect("https://ha.sumemail.com", "") == "https://ha.sumemail.com"


def test_malformed_pairs_are_ignored():
    assert redirects("nonsense,=x,https://a.b=") == {}
