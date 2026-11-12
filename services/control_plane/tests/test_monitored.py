"""The monitored-service list comes from the containers' own labels, not from code."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.control_plane.monitored import BadHealthLabel, describe, monitored, parse_health_label


def _container(name, labels=None, status="running", network="sharedllm_default", health=None, exit_code=0):
    state = {"Status": status, "ExitCode": exit_code, "StartedAt": "2026-01-01T00:00:00Z"}
    if health:
        state["Health"] = {"Status": health}
    return SimpleNamespace(name=name, status=status, labels=labels or {},
                           attrs={"State": state, "HostConfig": {"NetworkMode": network}})


def test_a_bridge_service_is_probed_by_its_compose_name():
    entry = describe(_container("sharedllm_widgets", {"sharedllm.health": "8099/health",
                                                      "com.docker.compose.service": "widgets-svc"}))
    assert entry["name"] == "widgets"
    assert entry["health_url"] == "http://widgets-svc:8099/health"
    assert entry["check"] == "http"


def test_a_host_network_service_is_probed_through_the_host():
    entry = describe(_container("sharedllm_hub", {"sharedllm.health": "8123/health"}, network="host"))
    assert entry["health_url"] == "http://host.docker.internal:8123/health"
    assert entry["network"] == "host"


def test_without_a_label_docker_decides_and_says_so():
    assert describe(_container("sharedllm_cache"))["check"] == "container state"
    entry = describe(_container("sharedllm_db", health="healthy"))
    assert entry["check"] == "docker healthcheck" and entry["health_url"] is None


def test_a_service_appears_without_any_code_change_and_others_are_ignored():
    names = [e["name"] for e in monitored([
        _container("sharedllm_zeta"), _container("sharedllm_alpha"), _container("someone_else"),
        _container("sharedllm_job", {"sharedllm.monitor": "false"}, status="exited"),
    ])]
    assert names == ["alpha", "zeta"]


def test_a_stopped_service_stays_on_the_list():
    entry = describe(_container("sharedllm_gone", status="exited", exit_code=137))
    assert entry["state"] == "exited" and entry["exit_code"] == 137


def test_a_bad_label_is_reported_not_skipped():
    entry = describe(_container("sharedllm_typo", {"sharedllm.health": "health:8000"}))
    assert entry["health_url"] is None and "not '<port>/<path>'" in entry["label_error"]


@pytest.mark.parametrize("value,expected", [("8009/health", (8009, "/health")), ("80", (80, "/")),
                                            (" 11438/v1/health ", (11438, "/v1/health"))])
def test_parse_health_label(value, expected):
    assert parse_health_label(value) == expected


@pytest.mark.parametrize("value", ["", "abc/health", "0/health", "70000/health", "http://x:1/health"])
def test_parse_health_label_rejects(value):
    with pytest.raises(BadHealthLabel):
        parse_health_label(value)
