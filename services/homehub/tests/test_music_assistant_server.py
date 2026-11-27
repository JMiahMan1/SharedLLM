"""The household Music Assistant server, from the hub's entry only (music_assistant_server.py).

* Where it is: the entry's address, or MA's published address when the entry's is loopback;
  never an environment variable; a clear error when there is no usable address.
* An imported entry's options are folded into its data once, so Configure edits the one value.
* A person's own MA account: found by username or made, a token issued, never two accounts.
* Playback through the hub can act as a person (MA's ``user`` argument), so it resumes from
  and records to their account. Every address and token is made up.
"""
from __future__ import annotations

import asyncio

import pytest

from services.homehub import music_assistant_server as mas
from services.homehub.core import ConfigEntry, HubError
from services.homehub.drivers.music_assistant import MusicAssistantDriver
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_drivers_media_music_assistant import STUDY, build


class Conn:
    def __init__(self, base_url="http://192.0.2.205:8095", connected=True, users=None, tokens=None, delay=0.0):
        self.server_info = {"base_url": base_url, "server_id": "srv1", "server_version": "2.10.9"}
        self.connected = connected
        self.users = users if users is not None else [{"user_id": "u-sam", "username": "Sam", "role": "user"}]
        self.tokens = tokens  # None: this MA cannot list tokens
        self.delay = delay
        self.sent: list[tuple[str, dict]] = []

    async def command(self, command, **args):
        self.sent.append((command, args))
        if self.delay:
            await asyncio.sleep(self.delay)
        if command == "auth/tokens":
            if self.tokens is None:
                return None
            return [t for t in self.tokens if t["user_id"] == args["user_id"]]
        if command == "auth/token/revoke":
            self.tokens = [t for t in self.tokens if t["token_id"] != args["token_id"]]
            return None
        if command == "auth/token/create" and self.tokens is not None:
            n = len([c for c, _ in self.sent if c == "auth/token/create"])
            self.tokens.append({"token_id": f"tok{n}", "user_id": args["user_id"], "name": args["name"],
                                "is_long_lived": True})
        if command == "auth/users":
            return list(self.users)
        if command == "auth/user/create":
            user = {"user_id": f"u-{args['username']}", "username": args["username"], "role": args["role"]}
            self.users.append(user)
            return user
        if command == "auth/token/create":
            return f"token-for-{args['user_id']}"
        return None


def hub_with(url="http://127.0.0.1:8095", conn=None, state="loaded"):
    hub, entry = make_hub("music_assistant", {"url": url, "token": "hub-token"}, entry_id="ma1")
    entry.state = state
    drv = MusicAssistantDriver(hub, entry)
    drv.conn = conn
    hub.drivers[entry.entry_id] = drv
    return hub, entry


def test_no_entry_means_no_household_server():
    hub, _ = make_hub("cast")
    out = mas.household_server(hub)
    assert out["configured"] is False and out["url"] is None and "no Music Assistant integration" in out["error"]


def test_a_loopback_entry_address_is_given_out_as_the_address_ma_publishes():
    hub, _ = hub_with(conn=Conn())
    out = mas.household_server(hub)
    assert out["url"] == "http://192.0.2.205:8095" and out["configured_url"] == "http://127.0.0.1:8095"
    assert "token" not in out and "hub-token" not in str(out)


def test_a_reachable_entry_address_is_used_as_it_is():
    hub, _ = hub_with(url="http://music.example.test:8095/", conn=Conn(base_url="http://other.example.test:8095"))
    assert mas.household_server(hub)["url"] == "http://music.example.test:8095"


def test_loopback_with_no_published_address_is_an_error_that_says_what_to_set():
    hub, _ = hub_with(conn=Conn(base_url=""))
    out = mas.household_server(hub)
    assert out["url"] is None and "only works on HomeHub's own host" in out["error"]
    hub, _ = hub_with(conn=None, state="setup_retry")
    assert mas.household_server(hub)["url"] is None


def test_an_entry_the_hub_cannot_reach_says_so():
    hub, entry = hub_with(url="http://music.example.test:8095", conn=Conn(connected=False), state="setup_retry")
    entry.reason = "Music Assistant did not answer"
    out = mas.household_server(hub)
    assert out["url"] == "http://music.example.test:8095"
    assert out["error"] == "HomeHub cannot reach Music Assistant at http://music.example.test:8095: Music Assistant did not answer"


def test_the_environment_is_never_read(monkeypatch):
    monkeypatch.setenv("MUSIC_ASSISTANT_URL", "http://env.example.test:8095")
    monkeypatch.setenv("MUSIC_ASSISTANT_TOKEN", "env-token")
    hub, entry = make_hub("music_assistant", {}, entry_id="ma1")
    out = mas.household_server(hub)
    assert out["url"] is None and "no server address" in out["error"]
    assert MusicAssistantDriver(hub, entry).url == ""


def test_fold_all_saves_only_the_entries_that_changed():
    hub, _ = make_hub("music_assistant", {"url": "http://d5369777-music-assistant:8094", "token": "old"},
                      options={"url": "http://127.0.0.1:8095", "token": "new"}, entry_id="ma1")
    hub.config_entries["c1"] = ConfigEntry("c1", "cast", options={"url": "keep"})
    saved = []

    class Store:
        def save_config_entry(self, entry):
            saved.append(entry.entry_id)

    assert mas.fold_all(hub, Store()) == ["ma1"] and saved == ["ma1"]
    assert hub.config_entries["ma1"].data == {"url": "http://127.0.0.1:8095", "token": "new"}
    assert hub.config_entries["ma1"].options == {} and hub.config_entries["c1"].options == {"url": "keep"}
    assert mas.fold_all(hub, Store()) == []


async def test_a_person_with_an_ma_account_gets_a_token_for_it_and_no_second_account():
    conn = Conn()
    hub, _ = hub_with(conn=conn)
    out = await mas.provision_account(hub, "sam", "Sam")
    assert out == {"username": "Sam", "user_id": "u-sam", "created": False, "token": "token-for-u-sam",
                   "revoked_tokens": 0}
    assert [c for c, _ in conn.sent] == ["auth/users", "auth/tokens", "auth/token/create"]
    assert conn.sent[-1][1] == {"name": "Jarvis", "user_id": "u-sam"}


async def test_a_person_without_one_gets_an_account_made_once():
    conn = Conn()
    hub, _ = hub_with(conn=conn)
    first = await mas.provision_account(hub, "Robin", "Robin R")
    assert first["created"] is True and first["user_id"] == "u-robin"
    create = next(a for c, a in conn.sent if c == "auth/user/create")
    assert create["username"] == "robin" and create["role"] == "user" and len(create["password"]) >= 8
    again = await mas.provision_account(hub, "robin")
    assert again["created"] is False and [c for c, _ in conn.sent].count("auth/user/create") == 1


async def test_a_new_token_replaces_earlier_jarvis_tokens_but_not_others():
    tokens = [{"token_id": "old-jarvis", "user_id": "u-sam", "name": "Jarvis", "is_long_lived": True},
              {"token_id": "phone", "user_id": "u-sam", "name": "Phone app", "is_long_lived": True},
              {"token_id": "session", "user_id": "u-sam", "name": "Jarvis", "is_long_lived": False}]
    conn = Conn(tokens=tokens)
    hub, _ = hub_with(conn=conn)
    out = await mas.provision_account(hub, "sam")
    assert out["revoked_tokens"] == 1
    assert sorted(t["token_id"] for t in conn.tokens) == ["phone", "session", "tok1"]
    assert ("auth/token/revoke", {"token_id": "old-jarvis"}) in conn.sent
    # the revoke comes after the new token exists, so a failure part way never leaves the person with none
    order = [c for c, _ in conn.sent]
    assert order.index("auth/token/create") < order.index("auth/token/revoke")


async def test_two_requests_for_one_person_at_once_leave_one_jarvis_token():
    conn = Conn(tokens=[], delay=0.01)
    hub, _ = hub_with(conn=conn)
    first, second = await asyncio.gather(mas.provision_account(hub, "sam"), mas.provision_account(hub, "Sam"))
    assert {first["token"], second["token"]} == {"token-for-u-sam"}
    jarvis = [t for t in conn.tokens if t["name"] == "Jarvis"]
    assert [t["token_id"] for t in jarvis] == ["tok2"]  # the second replaced the first, they did not pile up
    order = [c for c, _ in conn.sent]
    assert order == ["auth/users", "auth/tokens", "auth/token/create",
                     "auth/users", "auth/tokens", "auth/token/create", "auth/token/revoke"]


async def test_guests_and_a_hub_without_ma_are_refused():
    hub, _ = hub_with(conn=Conn(users=[{"user_id": "u-g", "username": "party_guest", "role": "guest"}]))
    with pytest.raises(mas.AccountError) as err:
        await mas.provision_account(hub, "party_guest")
    assert err.value.status == 409
    hub, _ = hub_with(conn=Conn(connected=False))
    with pytest.raises(mas.AccountError) as err:
        await mas.provision_account(hub, "sam")
    assert err.value.status == 503


async def test_a_new_address_or_token_ma_refuses_is_not_saved(monkeypatch):
    from services.homehub.flows import NeedsInput

    class Refusing:
        def __init__(self, *a):
            pass

        async def connect(self):
            raise HubError("Music Assistant refused the token")

        async def close(self):
            pass

    monkeypatch.setattr("services.homehub.drivers.music_assistant.MAConnection", Refusing)
    with pytest.raises(NeedsInput) as err:
        await mas.check_music_assistant({"url": "http://music.example.test:8095", "token": "bad"})
    assert err.value.error == "Not saved: Music Assistant refused the token"


async def test_playback_through_the_hub_can_act_as_a_person():
    hub, drv, made = build()
    await drv.async_setup()
    c = made["c"]
    await hub.call_service("music_assistant", "play_media", {
        "entity_id": "media_player.study_speaker", "media_id": "library://audiobook/9", "user": "u-sam"})
    assert c.sent[-1] == ("player_queues/play_media", {"queue_id": STUDY, "media": ["library://audiobook/9"],
                                                       "user": "u-sam"})
    await hub.call_service("music_assistant", "play_media", {
        "entity_id": "media_player.study_speaker", "media_id": "library://track/1"})
    assert "user" not in c.sent[-1][1]  # the hub's own login when nobody is named


async def test_configure_tries_a_changed_address_before_saving(monkeypatch):
    """Integrations > Music Assistant > Configure runs the check on a new url or token (entries._check_changes)."""
    from services.homehub import entries, manual_flows
    from services.homehub.flows import NeedsInput

    assert manual_flows.RECONFIGURE_CHECKS["music_assistant"][0] == ("url", "token")
    seen = []

    async def refuse(data):
        seen.append(dict(data))
        raise NeedsInput([], "no", error="Not saved: Music Assistant at http://bad.example.test:8095 did not answer")

    monkeypatch.setitem(manual_flows.RECONFIGURE_CHECKS, "music_assistant", (("url", "token"), refuse))
    with pytest.raises(entries.EntryError) as err:
        await entries._check_changes("music_assistant", {"url": "http://bad.example.test:8095"},
                                     {"url": "http://bad.example.test:8095", "token": "hub-token"})
    assert "did not answer" in str(err.value) and seen[0]["token"] == "hub-token"
    await entries._check_changes("music_assistant", {"title": "x"}, {"url": "u"})  # nothing it checks changed
    assert len(seen) == 1


class AbsProviderConn(Conn):
    """Fake MA connection that also supports config/providers, setup flows, and auth/user/update."""

    def __init__(self, *, users=None, providers=None, fail_submit=None):
        super().__init__(users=users)
        self.providers = list(providers) if providers is not None else [
            {"instance_id": "opensubsonic--m1", "domain": "opensubsonic", "type": "music", "enabled": True},
            {"instance_id": "radiobrowser--r1", "domain": "radiobrowser", "type": "music", "enabled": True},
            {"instance_id": "audiobookshelf--jer1", "domain": "audiobookshelf", "type": "music", "enabled": True, "name": "Audiobookshelf (jeremiah)"},
            {"instance_id": "chromecast--p1", "domain": "chromecast", "type": "player", "enabled": True},
        ]
        self.fail_submit = fail_submit
        self._flow_seq = 0
        self._flows: dict[str, dict] = {}
        self.saved_names: dict[str, str] = {}

    async def command(self, command, **args):
        if command == "config/providers":
            self.sent.append((command, args))
            return [dict(p) for p in self.providers]
        if command == "config/providers/setup":
            self.sent.append((command, args))
            self._flow_seq += 1
            fid = f"flow-{self._flow_seq}"
            self._flows[fid] = {"mode": "setup", "domain": args["provider_domain"]}
            return {"flow_id": fid, "type": "form", "result": None}
        if command == "config/providers/reconfigure":
            self.sent.append((command, args))
            self._flow_seq += 1
            fid = f"flow-{self._flow_seq}"
            self._flows[fid] = {"mode": "reconfigure", "instance_id": args["instance_id"]}
            return {"flow_id": fid, "type": "form", "result": None}
        if command == "config/flows/submit":
            self.sent.append((command, args))
            if self.fail_submit:
                return {"flow_id": args["flow_id"], "type": "form", "errors": {"base": self.fail_submit}}
            flow = self._flows[args["flow_id"]]
            if flow["mode"] == "setup":
                iid = f"audiobookshelf--new{self._flow_seq}"
                self.providers.append({
                    "instance_id": iid,
                    "domain": "audiobookshelf",
                    "type": "music",
                    "enabled": True,
                    "name": None,
                })
                return {"flow_id": args["flow_id"], "type": "finish", "result": {"instance_id": iid}}
            return {"flow_id": args["flow_id"], "type": "finish", "result": {"instance_id": flow["instance_id"]}}
        if command == "config/flows/abort":
            self.sent.append((command, args))
            return None
        if command == "config/providers/save":
            self.sent.append((command, args))
            iid = args["instance_id"]
            if "name" in (args.get("values") or {}):
                self.saved_names[iid] = args["values"]["name"]
                for p in self.providers:
                    if p["instance_id"] == iid:
                        p["name"] = args["values"]["name"]
            return {"instance_id": iid}
        if command == "config/providers/remove":
            self.sent.append((command, args))
            self.providers = [p for p in self.providers if p["instance_id"] != args["instance_id"]]
            return None
        if command == "auth/user/update":
            self.sent.append((command, args))
            for u in self.users:
                if u["user_id"] == args["user_id"]:
                    u["provider_filter"] = list(args["provider_filter"])
            return None
        return await super().command(command, **args)


async def test_new_ma_account_starts_with_shared_music_provider_filter_excluding_abs():
    conn = AbsProviderConn(users=[])
    hub, _ = hub_with(conn=conn)
    out = await mas.provision_account(hub, "michele", "Michele")
    assert out["created"] is True
    create = next(a for c, a in conn.sent if c == "auth/user/create")
    assert create["provider_filter"] == ["opensubsonic--m1", "radiobrowser--r1"]


async def test_sync_user_abs_creates_dedicated_provider_and_scopes_filter_for_password_login():
    conn = AbsProviderConn(users=[
        {"user_id": "u-jer", "username": "jeremiah", "role": "admin", "provider_filter": ["audiobookshelf--jer1", "opensubsonic--m1", "radiobrowser--r1"]},
        {"user_id": "u-zach", "username": "zachary", "role": "user", "provider_filter": []},
    ])
    hub, _ = hub_with(conn=conn)
    out = await mas.sync_user_audiobookshelf_provider(
        hub,
        username="zachary",
        display_name="Zachary",
        abs_url="https://abs.example.test/",
        abs_username="zachary",
        abs_password="secret-pw",
    )
    assert out["action"] == "created"
    assert out["abs_instance_id"] == "audiobookshelf--new1"
    assert out["provider_filter"] == ["audiobookshelf--new1", "opensubsonic--m1", "radiobrowser--r1"]
    assert "audiobookshelf--jer1" not in out["provider_filter"]
    assert conn.saved_names["audiobookshelf--new1"] == "Audiobookshelf (zachary)"
    submit = next(a for c, a in conn.sent if c == "config/flows/submit")
    # Password auth MUST explicitly pass api_token=None because MA checks `if token_api is not None`.
    assert submit["values"] == {
        "url": "https://abs.example.test",
        "verify_ssl": True,
        "api_token": None,
        "username": "zachary",
        "password": "secret-pw",
    }


async def test_sync_user_abs_reconfigures_existing_owned_provider_without_duplicating():
    conn = AbsProviderConn(users=[
        {"user_id": "u-jer", "username": "jeremiah", "role": "admin", "provider_filter": ["audiobookshelf--jer1", "opensubsonic--m1", "spotify--personal"]},
    ])
    hub, _ = hub_with(conn=conn)
    out = await mas.sync_user_audiobookshelf_provider(
        hub,
        username="jeremiah",
        abs_url="https://abs.example.test",
        abs_api_key="jer-api-key",
    )
    assert out["action"] == "updated"
    assert out["abs_instance_id"] == "audiobookshelf--jer1"
    # Preserves personal non-ABS providers like spotify--personal!
    assert out["provider_filter"] == ["audiobookshelf--jer1", "opensubsonic--m1", "radiobrowser--r1", "spotify--personal"]
    assert "config/providers/setup" not in [c for c, _ in conn.sent]
    assert "config/providers/reconfigure" in [c for c, _ in conn.sent]


async def test_sync_user_without_abs_credentials_strips_other_users_abs_from_filter():
    conn = AbsProviderConn(users=[
        {"user_id": "u-michele", "username": "michele", "role": "admin", "provider_filter": []},
    ])
    hub, _ = hub_with(conn=conn)
    out = await mas.sync_user_audiobookshelf_provider(hub, username="michele")
    assert out["action"] == "scoped"
    assert out["abs_instance_id"] is None
    assert out["provider_filter"] == ["opensubsonic--m1", "radiobrowser--r1"]
    assert out["filter_updated"] is True


async def test_sync_user_abs_aborts_flow_when_ma_refuses_credentials():
    conn = AbsProviderConn(
        users=[{"user_id": "u-zach", "username": "zachary", "role": "user", "provider_filter": []}],
        fail_submit="login_failed",
    )
    hub, _ = hub_with(conn=conn)
    with pytest.raises(mas.AccountError) as err:
        await mas.sync_user_audiobookshelf_provider(
            hub,
            username="zachary",
            abs_url="https://abs.example.test",
            abs_api_key="bad-key",
        )
    assert err.value.status == 422
    assert any(c == "config/flows/abort" for c, _ in conn.sent)


def test_the_accounts_list_says_who_exists_and_never_a_password():
    hub, _ = hub_with(conn=Conn(users=[
        {"user_id": "u1", "username": "Sam", "role": "user", "enabled": True, "password": "secret-hash"},
        {"user_id": "u2", "username": "robin", "role": "admin", "enabled": False},
    ]))
    out = asyncio.run(mas.list_accounts(hub))

    assert [u["username"] for u in out["users"]] == ["Sam", "robin"]
    assert out["users"][1]["role"] == "admin" and out["users"][1]["enabled"] is False
    assert out["server"] == "http://192.0.2.205:8095"
    assert "secret-hash" not in str(out)


def test_setting_a_password_matches_the_name_in_any_case_and_sends_it_once():
    conn = Conn(users=[{"user_id": "u1", "username": "Sam", "role": "user"}])
    hub, _ = hub_with(conn=conn)

    out = asyncio.run(mas.set_password(hub, "sam", "long-enough"))

    assert out == {"username": "Sam", "user_id": "u1", "changed": True}
    assert ("auth/user/update", {"user_id": "u1", "password": "long-enough"}) in conn.sent
    assert "long-enough" not in str(out)


def test_a_short_password_is_refused_before_music_assistant_sees_it():
    conn = Conn(users=[{"user_id": "u1", "username": "Sam", "role": "user"}])
    hub, _ = hub_with(conn=conn)

    with pytest.raises(mas.AccountError) as err:
        asyncio.run(mas.set_password(hub, "sam", "short"))
    assert err.value.status == 400 and "8 characters" in str(err.value)
    assert not [c for c, _ in conn.sent if c == "auth/user/update"]


def test_a_password_for_nobody_says_there_is_no_such_account():
    hub, _ = hub_with(conn=Conn(users=[{"user_id": "u1", "username": "Sam", "role": "user"}]))

    with pytest.raises(mas.AccountError) as err:
        asyncio.run(mas.set_password(hub, "nobody", "long-enough"))
    assert err.value.status == 404 and "no account called nobody" in str(err.value)


def test_music_assistant_refusing_the_password_is_reported_with_its_reason():
    class Refusing(Conn):
        async def command(self, command, **args):
            if command == "auth/user/update":
                raise HubError("password is too weak")
            return await super().command(command, **args)

    hub, _ = hub_with(conn=Refusing(users=[{"user_id": "u1", "username": "Sam", "role": "user"}]))

    with pytest.raises(mas.AccountError) as err:
        asyncio.run(mas.set_password(hub, "sam", "long-enough"))
    assert "Music Assistant refused: password is too weak" in str(err.value)
