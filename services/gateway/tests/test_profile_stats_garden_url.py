"""The garden card's service address: services.config when the gateway module does not carry it."""
from services import config
from services.gateway import main as gateway
from services.gateway import profile_stats


def test_every_stats_source_has_an_address():
    for attr in ("IDENTITY_SVC", "BIBLE_SVC", "GEO_SVC", "HEALTH_SVC", "GARDEN_SVC"):
        assert profile_stats._svc(attr).startswith("http"), attr


def test_the_garden_address_comes_from_config(monkeypatch):
    monkeypatch.delattr(gateway, "GARDEN_SVC", raising=False)
    assert profile_stats._svc("GARDEN_SVC") == str(config.GARDEN_SVC).rstrip("/")


def test_the_gateway_value_wins_so_tests_can_point_it_elsewhere(monkeypatch):
    monkeypatch.setattr(gateway, "GARDEN_SVC", "http://garden.test:9/", raising=False)
    assert profile_stats._svc("GARDEN_SVC") == "http://garden.test:9"
