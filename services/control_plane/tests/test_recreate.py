"""Recreating a container on a new image keeps everything the old one had.

Incident 2026-11-22: an update recreated identity, attached its network and
"identity" alias only after create, and the name stopped resolving; Caddy could
not proxy host port 8001 to it and execution (host network) failed its startup.
These tests drive services.control_plane.recreate against a fake Docker that
behaves like the daemon where it matters: create builds the container from the
body it is given, endpoints in NetworkingConfig are attached at create, and the
image supplies Env/Labels the container did not set.
"""

from __future__ import annotations

import copy
import itertools
import json

import pytest

from services.control_plane import recreate

OLD_IMAGE = "sha256:" + "a" * 64
NEW_IMAGE = "sha256:" + "b" * 64

IMAGES = {
    OLD_IMAGE: {"Env": ["PATH=/usr/bin", "GIT_SHA=old"], "Labels": {"org.opencontainers.image.revision": "old"},
                "Cmd": ["python", "-m", "app"], "User": ""},
    NEW_IMAGE: {"Env": ["PATH=/usr/bin", "GIT_SHA=new"], "Labels": {"org.opencontainers.image.revision": "new"},
                "Cmd": ["python", "-m", "app"], "User": ""},
}

_ids = itertools.count(1)


def _id() -> str:
    return f"{next(_ids):012x}" + "0" * 52


class FakeImage:
    def __init__(self, image_id):
        self.id = image_id
        self.attrs = {"Config": copy.deepcopy(IMAGES[image_id])}


class FakeContainer:
    def __init__(self, daemon, cid, name, attrs):
        self.daemon, self.id, self.name, self.attrs = daemon, cid, name, attrs

    @property
    def image(self):
        return FakeImage(self.attrs["Image"])

    def reload(self):
        self.attrs["Name"] = "/" + self.name

    def start(self):
        self.daemon.events.append(("start", self.name))
        state = self.attrs["State"]
        state["Status"] = "running"
        outcome = self.daemon.health.get(self.attrs["Image"], "healthy")
        if outcome == "exit":
            state["Status"] = "exited"
            state["ExitCode"] = 1
        elif self.attrs["Config"].get("Healthcheck"):
            state["Health"] = {"Status": outcome}

    def stop(self, timeout=10):
        self.daemon.events.append(("stop", self.name))
        self.attrs["State"] = {"Status": "exited", "ExitCode": 0}

    def rename(self, name):
        self.daemon.events.append(("rename", self.name, name))
        self.name = name

    def remove(self, force=False):
        self.daemon.events.append(("remove", self.name))
        self.daemon.containers.pop(self.id, None)


class FakeNetwork:
    def __init__(self, daemon, name):
        self.daemon, self.name = daemon, name

    def connect(self, container, aliases=None, ipv4_address=None):
        self.daemon.events.append(("connect", container.name, self.name))
        container.attrs["NetworkSettings"]["Networks"][self.name] = _endpoint_attrs(
            container, aliases or [], ipv4_address,
        )

    def disconnect(self, container):
        self.daemon.events.append(("disconnect", container.name, self.name))
        container.attrs["NetworkSettings"]["Networks"].pop(self.name, None)


def _endpoint_attrs(container, aliases, ipv4):
    return {
        "Aliases": list(aliases),
        "DNSNames": [container.name, *aliases, container.id[:12]],
        "IPAMConfig": {"IPv4Address": ipv4} if ipv4 else None,
        "IPAddress": ipv4 or "172.26.0.9",
    }


class FakeApi:
    def __init__(self, daemon):
        self.daemon = daemon

    def create_container_from_config(self, body, name=None):
        d = self.daemon
        d.bodies.append(copy.deepcopy(body))
        d.events.append(("create", name))
        cid = _id()
        config = {k: v for k, v in copy.deepcopy(body).items() if k not in ("HostConfig", "NetworkingConfig")}
        image = config["Image"]
        image_config = IMAGES[image]
        # The daemon merges the image's defaults under what the body sets.
        set_keys = {e.split("=", 1)[0] for e in config.get("Env") or []}
        config["Env"] = (config.get("Env") or []) + [
            e for e in image_config["Env"] if e.split("=", 1)[0] not in set_keys
        ]
        config["Labels"] = {**image_config["Labels"], **(config.get("Labels") or {})}
        for key in ("Cmd", "User"):
            config.setdefault(key, image_config[key])
        host_config = copy.deepcopy(body.get("HostConfig") or {})
        for key in d.drop_host_keys:
            host_config.pop(key, None)
        attrs = {
            "Id": cid,
            "Name": "/" + name,
            "Image": image,
            "Config": config,
            "HostConfig": host_config,
            "State": {"Status": "created"},
            "RestartCount": 0,
            "Mounts": _mounts(host_config),
            "NetworkSettings": {"Networks": {}},
        }
        container = FakeContainer(d, cid, name, attrs)
        endpoints = (body.get("NetworkingConfig") or {}).get("EndpointsConfig") or {}
        if d.first_endpoint_only:
            endpoints = dict(list(endpoints.items())[:1])
        for net_name, ep in endpoints.items():
            attrs["NetworkSettings"]["Networks"][net_name] = _endpoint_attrs(
                container, ep.get("Aliases") or [], (ep.get("IPAMConfig") or {}).get("IPv4Address"),
            )
        d.containers[cid] = container
        return {"Id": cid}


def _mounts(host_config):
    mounts = []
    for bind in host_config.get("Binds") or []:
        src, dst, *_ = bind.split(":")
        mounts.append({"Type": "bind", "Source": src, "Destination": dst, "RW": True})
    for m in host_config.get("Mounts") or []:
        mounts.append({"Type": "volume", "Name": m["Source"], "Source": "/var/lib/docker/volumes/x",
                       "Destination": m["Target"], "RW": True})
    return mounts


class _Lookup:
    def __init__(self, daemon):
        self.daemon = daemon

    def get(self, key):
        for c in self.daemon.containers.values():
            if key in (c.id, c.name):
                return c
        raise KeyError(key)


class _Networks:
    def __init__(self, daemon):
        self.daemon = daemon

    def get(self, name):
        return FakeNetwork(self.daemon, name)


class FakeDocker:
    def __init__(self):
        self.containers: dict[str, FakeContainer] = {}
        self.events: list[tuple] = []
        self.bodies: list[dict] = []
        self.health: dict[str, str] = {}
        self.drop_host_keys: set[str] = set()
        self.first_endpoint_only = False
        self.api = FakeApi(self)


class Client:
    """The object recreate receives as ``client``."""

    def __init__(self, daemon: FakeDocker):
        self.daemon = daemon
        self.api = daemon.api
        self.containers = _Lookup(daemon)
        self.networks = _Networks(daemon)


HEALTHCHECK = {"Test": ["CMD", "curl", "-f", "http://localhost:8001/health"], "Interval": 5_000_000_000,
               "Timeout": 3_000_000_000, "Retries": 10, "StartPeriod": 10_000_000_000}


def _add_old(daemon, *, name="sharedllm_identity", service="identity", network_mode="sharedllm_default",
             networks=None, port_bindings=None, static_ip=None, labels=None):
    cid = _id()
    config = {
        "Hostname": cid[:12],
        "Image": "ghcr.io/example/sharedllm-identity:latest",
        "Env": ["PATH=/usr/bin", "GIT_SHA=old", "HOME=/tmp", "IDENTITY_DATABASE_URL=sqlite:////data/identity.db"],
        "Cmd": ["python", "-m", "app"],
        "User": "1000:1000",
        "Labels": {"org.opencontainers.image.revision": "old", "com.docker.compose.service": service,
                   "com.docker.compose.project": "sharedllm", "sharedllm.health": "8001/health", **(labels or {})},
        "Healthcheck": HEALTHCHECK,
        "ExposedPorts": {"8001/tcp": {}},
    }
    host_config = {
        "NetworkMode": network_mode,
        "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        "Binds": ["/srv/sharedllm/data/identity:/data:rw", "/etc/localtime:/etc/localtime:ro"],
        "Mounts": [{"Type": "volume", "Source": "sharedllm_cache", "Target": "/cache"}],
        "ExtraHosts": ["host.docker.internal:host-gateway"],
        "Dns": ["172.26.0.254"],
        "CapAdd": ["NET_ADMIN"],
        "Devices": [{"PathOnHost": "/dev/snd", "PathInContainer": "/dev/snd", "CgroupPermissions": "rwm"}],
        "Memory": 1073741824,
        "PortBindings": port_bindings or {},
    }
    attrs = {
        "Id": cid, "Name": "/" + name, "Image": OLD_IMAGE, "Config": config, "HostConfig": host_config,
        "State": {"Status": "running", "Health": {"Status": "healthy"}}, "RestartCount": 0,
        "Mounts": _mounts(host_config), "NetworkSettings": {"Networks": {}},
    }
    old = FakeContainer(daemon, cid, name, attrs)
    if network_mode != "host":
        for net in networks or [network_mode]:
            attrs["NetworkSettings"]["Networks"][net] = _endpoint_attrs(
                old, [name, service, cid[:12]], static_ip if net == network_mode else None,
            )
            if not static_ip:
                attrs["NetworkSettings"]["Networks"][net]["IPAMConfig"] = None
    else:
        attrs["NetworkSettings"]["Networks"]["host"] = {"Aliases": None, "IPAddress": ""}
    daemon.containers[cid] = old
    return old


@pytest.fixture(name="daemon")
def daemon_fixture():
    recreate._alerted.clear()
    return FakeDocker()


def _run(daemon, old, **kwargs):
    notices = []
    result = recreate.recreate_container(
        Client(daemon), old, NEW_IMAGE, notify=notices.append, sleep=lambda s: None, **kwargs,
    )
    return result, notices


def _current(daemon, name):
    return Client(daemon).containers.get(name)


def test_networks_and_aliases_are_attached_at_create_not_after(daemon):
    old = _add_old(daemon)
    _run(daemon, old)
    body = daemon.bodies[0]
    endpoint = body["NetworkingConfig"]["EndpointsConfig"]["sharedllm_default"]
    assert {"sharedllm_identity", "identity"} <= set(endpoint["Aliases"])
    assert not any(e[0] == "connect" for e in daemon.events), daemon.events


def test_everything_the_old_container_had_is_carried_over(daemon):
    old = _add_old(daemon, networks=["sharedllm_default", "sharedllm_extra"])
    old_host_config = copy.deepcopy(old.attrs["HostConfig"])
    result, notices = _run(daemon, old)

    new = _current(daemon, "sharedllm_identity")
    assert new is result["container"] and new.id != old.id
    assert new.attrs["Image"] == NEW_IMAGE
    hc = new.attrs["HostConfig"]
    for key in ("NetworkMode", "RestartPolicy", "Binds", "Mounts", "ExtraHosts", "Dns", "CapAdd", "Devices",
                "Memory", "PortBindings"):
        assert hc[key] == old_host_config[key], key
    config = new.attrs["Config"]
    assert config["Healthcheck"] == HEALTHCHECK
    assert config["User"] == "1000:1000"
    assert config["ExposedPorts"] == {"8001/tcp": {}}
    assert {"HOME=/tmp", "IDENTITY_DATABASE_URL=sqlite:////data/identity.db"} <= set(config["Env"])
    assert config["Labels"]["sharedllm.health"] == "8001/health"
    assert config["Labels"]["com.docker.compose.service"] == "identity"
    nets = new.attrs["NetworkSettings"]["Networks"]
    for net in ("sharedllm_default", "sharedllm_extra"):
        assert {"identity", "sharedllm_identity"} <= set(nets[net]["Aliases"])
    assert {m["Destination"] for m in new.attrs["Mounts"]} == {"/data", "/etc/localtime", "/cache"}
    assert notices == []


def test_values_the_old_image_baked_in_give_way_to_the_new_image(daemon):
    old = _add_old(daemon)
    _run(daemon, old)
    body = daemon.bodies[0]
    assert "GIT_SHA=old" not in body["Env"]
    assert "org.opencontainers.image.revision" not in body["Labels"]
    assert "Cmd" not in body  # the image's own Cmd: the new image decides
    assert body.get("Hostname") is None  # the old container id is not a hostname to keep
    new = _current(daemon, "sharedllm_identity")
    assert "GIT_SHA=new" in new.attrs["Config"]["Env"]
    assert new.attrs["Config"]["Labels"]["org.opencontainers.image.revision"] == "new"


START_FIRST = {"sharedllm.update": "start-first"}


def _first(events, kind, name=None):
    return next(i for i, e in enumerate(events) if e[0] == kind and (name is None or e[1] == name))


def test_by_default_the_old_container_stops_before_the_new_one_is_created(daemon):
    """Two copies of a stateful service on the same SQLite file risk concurrent
    migrations, duplicated background jobs and DB locks: stop-first is the default."""
    old = _add_old(daemon)
    result, _ = _run(daemon, old)
    assert result["strategy"] == "stop-first"
    events = daemon.events
    assert events.index(("stop", "sharedllm_identity")) < _first(events, "create") < _first(events, "start")
    assert _first(events, "create") == events.index(("create", "sharedllm_identity"))
    assert old.id not in daemon.containers


def test_a_stateful_service_is_never_started_first(daemon):
    for name, service in (("sharedllm_identity", "identity"), ("sharedllm_bible", "bible"),
                          ("sharedllm_garden", "garden"), ("sharedllm_storage", "storage")):
        d = FakeDocker()
        old = _add_old(d, name=name, service=service)
        assert recreate.choose_strategy(recreate.snapshot_container(old))[0] == "stop-first"
        result, _ = _run(d, old)
        assert result["strategy"] == "stop-first"
        assert d.events.index(("stop", name)) < _first(d.events, "start")


def test_an_opted_in_stateless_service_starts_first_and_the_old_one_serves_until_the_new_one_is_healthy(daemon):
    old = _add_old(daemon, name="sharedllm_ui", service="ui", labels=START_FIRST)
    result, _ = _run(daemon, old)
    assert result["strategy"] == "start-first"
    events = daemon.events
    assert events[0][0] == "create" and events[0][1].startswith("sharedllm_ui_next_")
    assert _first(events, "start") < events.index(("stop", "sharedllm_ui")), events
    assert ("remove", next(e[2] for e in events if e[0] == "rename" and e[1] == "sharedllm_ui")) in events
    assert _current(daemon, "sharedllm_ui") is result["container"]
    assert old.id not in daemon.containers


@pytest.mark.parametrize("kwargs, blocker", [
    ({"network_mode": "host"}, "host network"),
    ({"port_bindings": {"8008/tcp": [{"HostIp": "", "HostPort": "8008"}]}}, "publishes host ports"),
    ({"static_ip": "172.26.0.250"}, "static IP"),
])
def test_an_opt_in_that_cannot_run_twice_still_stops_first(daemon, kwargs, blocker):
    old = _add_old(daemon, name="sharedllm_ui", service="ui", labels=START_FIRST, **kwargs)
    strategy, why = recreate.choose_strategy(recreate.snapshot_container(old))
    assert strategy == "stop-first" and blocker in why


def test_published_ports_are_kept_and_the_old_one_stops_first(daemon):
    bindings = {"8001/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8001"}]}
    old = _add_old(daemon, port_bindings=bindings)
    result, notices = _run(daemon, old)
    assert result["strategy"] == "stop-first"
    new = _current(daemon, "sharedllm_identity")
    assert new.attrs["HostConfig"]["PortBindings"] == bindings
    assert daemon.events.index(("stop", "sharedllm_identity")) < next(
        i for i, e in enumerate(daemon.events) if e[0] == "create"
    )
    assert notices == []


def test_a_host_network_container_keeps_host_mode(daemon):
    old = _add_old(daemon, name="sharedllm_execution", service="execution", network_mode="host")
    result, notices = _run(daemon, old)
    assert result["strategy"] == "stop-first"
    body = daemon.bodies[0]
    assert body["HostConfig"]["NetworkMode"] == "host"
    assert "NetworkingConfig" not in body
    new = _current(daemon, "sharedllm_execution")
    assert new.attrs["HostConfig"]["NetworkMode"] == "host"
    assert not any(e[0] in ("connect", "disconnect") for e in daemon.events)
    assert notices == []


def test_a_static_ip_is_kept(daemon):
    old = _add_old(daemon, name="sharedllm_dns", service="dns", static_ip="172.26.0.254")
    result, _ = _run(daemon, old)
    assert result["strategy"] == "stop-first"
    endpoint = daemon.bodies[0]["NetworkingConfig"]["EndpointsConfig"]["sharedllm_default"]
    assert endpoint["IPAMConfig"] == {"IPv4Address": "172.26.0.254"}
    new = _current(daemon, "sharedllm_dns")
    assert new.attrs["NetworkSettings"]["Networks"]["sharedllm_default"]["IPAMConfig"]["IPv4Address"] == "172.26.0.254"


def test_a_dynamic_ip_is_not_pinned(daemon):
    old = _add_old(daemon)
    _run(daemon, old)
    endpoint = daemon.bodies[0]["NetworkingConfig"]["EndpointsConfig"]["sharedllm_default"]
    assert "IPAMConfig" not in endpoint


def test_an_older_daemon_gets_the_other_networks_before_start(daemon):
    daemon.first_endpoint_only = True
    old = _add_old(daemon, networks=["sharedllm_default", "sharedllm_extra"])
    _run(daemon, old)
    events = daemon.events
    connect = next(i for i, e in enumerate(events) if e[0] == "connect")
    start = next(i for i, e in enumerate(events) if e[0] == "start")
    assert connect < start and events[connect][2] == "sharedllm_extra"


def test_lost_port_bindings_roll_back_and_alert_once(daemon):
    bindings = {"8001/tcp": [{"HostIp": "", "HostPort": "8001"}]}
    daemon.drop_host_keys = {"PortBindings"}
    old = _add_old(daemon, port_bindings=bindings)
    with pytest.raises(recreate.RecreateFailed, match="PortBindings"):
        _run(daemon, old)
    current = _current(daemon, "sharedllm_identity")
    assert current is old and old.attrs["State"]["Status"] == "running"
    assert old.attrs["Image"] == OLD_IMAGE
    assert len(daemon.containers) == 1  # the refused container is gone
    assert not any(e[0] == "start" and e[1] != "sharedllm_identity" for e in daemon.events)

    # A second try at the same image logs and alerts no more.
    notices = []
    with pytest.raises(recreate.RecreateFailed):
        recreate.recreate_container(Client(daemon), old, NEW_IMAGE, notify=notices.append, sleep=lambda s: None)
    assert notices == []


def test_the_rollback_notice_goes_to_the_admins(daemon):
    daemon.drop_host_keys = {"ExtraHosts"}
    old = _add_old(daemon)
    notices = []
    with pytest.raises(recreate.RecreateFailed):
        recreate.recreate_container(Client(daemon), old, NEW_IMAGE, notify=notices.append, sleep=lambda s: None)
    assert len(notices) == 1
    assert notices[0]["tag"] == "admin:update:sharedllm_identity"
    assert "ExtraHosts" in notices[0]["body"]


def test_an_unhealthy_start_first_container_never_replaces_the_old_one(daemon):
    daemon.health[NEW_IMAGE] = "unhealthy"
    old = _add_old(daemon, name="sharedllm_ui", service="ui", labels=START_FIRST)
    with pytest.raises(recreate.RecreateFailed, match="unhealthy"):
        _run(daemon, old)
    assert ("stop", "sharedllm_ui") not in daemon.events  # old one served throughout
    assert _current(daemon, "sharedllm_ui") is old
    assert len(daemon.containers) == 1


def test_an_unhealthy_stop_first_container_is_rolled_back_to_the_old_one(daemon):
    daemon.health[NEW_IMAGE] = "unhealthy"
    old = _add_old(daemon)
    notices = []
    with pytest.raises(recreate.RecreateFailed, match="unhealthy"):
        recreate.recreate_container(Client(daemon), old, NEW_IMAGE, notify=notices.append, sleep=lambda s: None)
    assert _current(daemon, "sharedllm_identity") is old
    assert old.attrs["State"]["Status"] == "running" and old.attrs["Image"] == OLD_IMAGE
    assert len(daemon.containers) == 1
    assert len(notices) == 1


def test_a_stop_first_container_that_crashes_is_restored(daemon):
    daemon.health[NEW_IMAGE] = "exit"
    old = _add_old(daemon, name="sharedllm_execution", service="execution", network_mode="host")
    with pytest.raises(recreate.RecreateFailed, match="exited"):
        _run(daemon, old)
    assert _current(daemon, "sharedllm_execution") is old
    assert old.attrs["State"]["Status"] == "running"
    assert old.name == "sharedllm_execution"


def test_a_new_container_that_never_turns_healthy_times_out(daemon):
    daemon.health[NEW_IMAGE] = "starting"
    old = _add_old(daemon)
    ticks = itertools.count(0, 10.0)
    with pytest.raises(recreate.RecreateFailed, match="not healthy after"):
        recreate.recreate_container(
            Client(daemon), old, NEW_IMAGE, sleep=lambda s: None, clock=lambda: next(ticks),
        )
    assert _current(daemon, "sharedllm_identity") is old


def test_the_health_wait_comes_from_the_containers_own_healthcheck():
    assert recreate.health_timeout({"Config": {"Healthcheck": HEALTHCHECK}}) == pytest.approx(10 + 10 * 8 + 5)
    assert recreate.health_timeout({"Config": {}}) == recreate.HEALTH_TIMEOUT_MIN_S


def test_the_snapshot_is_json_for_the_self_update_helper(daemon):
    old = _add_old(daemon)
    snap = recreate.snapshot_container(old)
    assert json.loads(json.dumps(snap))["name"] == "sharedllm_identity"
    assert snap["image_id"] == OLD_IMAGE
