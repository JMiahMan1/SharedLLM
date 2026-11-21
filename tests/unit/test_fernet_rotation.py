"""FERNET_KEY rotation: the key ring every service seals with, and the re-encryption tool.

Keys here are generated per test; no value is ever printed by the code under test, and the
assertions check that its reports carry counts and places only.
"""
import json
import sqlite3

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from services.common import fernet_rotation
from services.common.fernet_keys import FernetKeyError, KeyRing, fingerprint, hmac_digest, parse_previous


@pytest.fixture
def keys():
    return Fernet.generate_key(), Fernet.generate_key()


def test_ring_seals_with_the_new_key_and_opens_the_old(keys):
    old, new = keys
    sealed_old = Fernet(old).encrypt(b"made-up secret")
    ring = KeyRing(new, [old])
    assert ring.rotating
    assert ring.decrypt(sealed_old) == b"made-up secret"
    fresh = ring.encrypt(b"made-up secret")
    assert Fernet(new).decrypt(fresh) == b"made-up secret"
    with pytest.raises(InvalidToken):
        Fernet(old).decrypt(fresh)
    assert ring.opens_with_primary(fresh) and not ring.opens_with_primary(sealed_old)


def test_digests_recognise_the_previous_key(keys):
    old, new = keys
    ring = KeyRing(new, [old])
    assert ring.digest("k") == hmac_digest(new, "k")
    assert ring.digests("k") == [hmac_digest(new, "k"), hmac_digest(old, "k")]


def test_a_malformed_previous_key_fails_fast_without_showing_it(keys):
    _, new = keys
    with pytest.raises(FernetKeyError) as exc:
        KeyRing.from_values(new.decode(), "not-a-key-value-xyz")
    assert "FERNET_KEY_PREVIOUS" in str(exc.value)
    assert "not-a-key-value-xyz" not in str(exc.value)


def test_missing_primary_is_an_error():
    with pytest.raises(FernetKeyError):
        KeyRing(None)


def test_previous_list_parsing_and_duplicates(keys):
    old, new = keys
    assert parse_previous(f" {old.decode()} , ,{new.decode()} ") == [old.decode(), new.decode()]
    ring = KeyRing(new, [new, old, old])
    assert len(ring.previous) == 1


def test_fingerprint_is_short_and_one_way(keys):
    old, _ = keys
    fp = fingerprint(old)
    assert len(fp) == 12 and fp not in old.decode()
    assert fingerprint(None) == ""


# -- the SQLite stores ----------------------------------------------------------------

def _make_db(path, old: bytes, extra_undecryptable: bool = False) -> str:
    f = Fernet(old)
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE user (id INTEGER PRIMARY KEY, username TEXT, api_key_enc TEXT, api_key_hash TEXT,
                           github_token_enc TEXT, note TEXT);
        CREATE TABLE globalsetting (id INTEGER PRIMARY KEY, key TEXT, value TEXT);
        CREATE TABLE blobs (name TEXT PRIMARY KEY, secret BLOB NOT NULL);
        CREATE TABLE apikey (id INTEGER PRIMARY KEY, key_hash TEXT);
        """
    )
    db.execute("INSERT INTO user VALUES (1, 'alpha', ?, ?, ?, 'plain text')",
               (f.encrypt(b"fake-api-key-1").decode(), hmac_digest(old, "fake-api-key-1"),
                f.encrypt(b"fake-gh-token").decode()))
    db.execute("INSERT INTO user VALUES (2, 'beta', NULL, NULL, NULL, NULL)")
    db.execute("INSERT INTO globalsetting VALUES (1, 'skylight_pass_enc', ?)", (f.encrypt(b"fake-pass").decode(),))
    db.execute("INSERT INTO globalsetting VALUES (2, 'theme', 'dark')")
    db.execute("INSERT INTO blobs VALUES ('ha_auth', ?)", (f.encrypt(b'{"tokens": {}}'),))
    if extra_undecryptable:
        db.execute("INSERT INTO globalsetting VALUES (3, 'broken_enc', ?)",
                   (Fernet(Fernet.generate_key()).encrypt(b"lost").decode(),))
    db.commit()
    db.close()
    return str(path)


def test_plan_apply_verify_round_trip(tmp_path, keys):
    old, new = keys
    path = _make_db(tmp_path / "id.db", old)

    # Before the switch: the current key opens everything (the preflight).
    plan = fernet_rotation.rotate_sqlite(path, KeyRing(old), "plan", "identity")
    assert plan.totals.tokens == 4 and plan.totals.primary == 4
    assert fernet_rotation.verdict(plan)[0] == 0

    ring = KeyRing(new, [old])
    pre = fernet_rotation.rotate_sqlite(path, ring, "verify", "identity")
    assert pre.totals.previous == 4
    assert fernet_rotation.verdict(pre)[0] == 4  # not done yet

    applied = fernet_rotation.rotate_sqlite(path, ring, "apply", "identity")
    assert applied.totals.rotated == 4
    assert applied.rehashed == 1

    done = fernet_rotation.rotate_sqlite(path, ring, "verify", "identity")
    assert done.totals.primary == 4 and done.totals.previous == 0 and done.rehashed == 0
    assert fernet_rotation.verdict(done)[0] == 0

    # The new key alone opens every value, with types kept, and the lookup hash follows the key.
    only_new = KeyRing(new)
    db = sqlite3.connect(path)
    enc, digest, gh = db.execute("SELECT api_key_enc, api_key_hash, github_token_enc FROM user WHERE id=1").fetchone()
    assert only_new.decrypt(enc.encode()) == b"fake-api-key-1"
    assert digest == hmac_digest(new, "fake-api-key-1")
    assert only_new.decrypt(gh.encode()) == b"fake-gh-token"
    (blob,) = db.execute("SELECT secret FROM blobs").fetchone()
    assert isinstance(blob, bytes) and only_new.decrypt(blob) == b'{"tokens": {}}'
    assert db.execute("SELECT note FROM user WHERE id=1").fetchone()[0] == "plain text"
    db.close()

    # Re-running is a no-op: an interrupted run can simply be resumed.
    again = fernet_rotation.rotate_sqlite(path, ring, "apply", "identity")
    assert again.totals.rotated == 0


def test_reports_carry_no_values(tmp_path, keys):
    old, new = keys
    path = _make_db(tmp_path / "id.db", old)
    report = fernet_rotation.rotate_sqlite(path, KeyRing(new, [old]), "apply", "identity")
    text = json.dumps(report.as_dict())
    for value in ("fake-api-key-1", "fake-gh-token", "fake-pass", "gAAAAA", old.decode(), new.decode()):
        assert value not in text


def test_an_unreadable_value_fails_and_is_named(tmp_path, keys):
    old, new = keys
    path = _make_db(tmp_path / "id.db", old, extra_undecryptable=True)
    report = fernet_rotation.rotate_sqlite(path, KeyRing(new, [old]), "verify", "identity")
    assert report.undecryptable == ["globalsetting.value rowid=3"]
    status, line = fernet_rotation.verdict(report)
    assert status == 3 and "open with no key" in line
    assert fernet_rotation.verdict(report, allow_undecryptable=True)[0] == 4


def test_apply_rolls_back_on_failure(tmp_path, keys, monkeypatch):
    old, new = keys
    path = _make_db(tmp_path / "id.db", old)
    ring = KeyRing(new, [old])
    calls = {"n": 0}
    real = ring.rotate

    def flaky(token):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk went away")
        return real(token)

    monkeypatch.setattr(ring, "rotate", flaky)
    with pytest.raises(RuntimeError):
        fernet_rotation.rotate_sqlite(path, ring, "apply", "identity")
    after = fernet_rotation.rotate_sqlite(path, KeyRing(new, [old]), "verify", "identity")
    assert after.totals.previous == 4  # nothing half-done


def test_homehub_scans_only_its_sealed_tables(tmp_path, keys):
    old, new = keys
    path = _make_db(tmp_path / "hub.db", old)
    report = fernet_rotation.run("homehub", "plan", KeyRing(new, [old]), env={"HOMEHUB_DB": path})
    assert set(report.columns) == {"blobs.secret"}


def test_non_sqlite_urls_are_refused():
    with pytest.raises(SystemExit):
        fernet_rotation.sqlite_path("postgresql://db/identity")
    assert fernet_rotation.sqlite_path("sqlite:////data/identity.db") == "/data/identity.db"


def test_missing_database_is_an_error(tmp_path, keys):
    _, new = keys
    with pytest.raises(SystemExit):
        fernet_rotation.run("workspace", "plan", KeyRing(new),
                            env={"WORKSPACE_DATABASE_URL": f"sqlite:///{tmp_path}/missing.db"})


# -- Redis (telemetry concern images) --------------------------------------------------

class FakeRedis:
    def __init__(self):
        self.data: dict[bytes, bytes] = {}
        self.keepttl: list[bool] = []

    def scan_iter(self, match, count=None):
        prefix = match.rstrip("*").encode()
        return [k for k in list(self.data) if k.startswith(prefix)]

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, keepttl=False):
        self.keepttl.append(keepttl)
        self.data[key] = value


def test_redis_concern_images_are_rotated_keeping_their_ttl(keys):
    old, new = keys
    fake = FakeRedis()
    fake.data[b"tel:concern-image:kid:1"] = Fernet(old).encrypt(b"aGVsbG8=")
    fake.data[b"tel:other"] = b"untouched"
    ring = KeyRing(new, [old])
    report = fernet_rotation.rotate_redis("redis://unused", ring, "apply", client=fake)
    assert report.totals.rotated == 1
    assert fake.keepttl == [True]
    assert KeyRing(new).decrypt(fake.data[b"tel:concern-image:kid:1"]) == b"aGVsbG8="
    assert fake.data[b"tel:other"] == b"untouched"


# -- identity during a rotation --------------------------------------------------------

def test_identity_finds_and_rehashes_a_key_hashed_under_the_old_key(keys, monkeypatch):
    old, new = keys
    from services.identity import crypto
    from services.identity import main as identity_main
    from services.identity.models import User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(crypto, "_ring", KeyRing(old))
    with Session(engine) as session:
        user = User(username="gamma")
        identity_main._store_user_api_key(user, "fake-key-gamma")
        session.add(user)
        session.commit()
        old_hash = user.api_key_hash

        monkeypatch.setattr(crypto, "_ring", KeyRing(new, [old]))
        found = identity_main._find_user_for_api_key(session, "fake-key-gamma")
        assert found is not None and found.username == "gamma"
        session.refresh(found)
        assert found.api_key_hash != old_hash
        assert found.api_key_hash == hmac_digest(new, "fake-key-gamma")
        assert crypto.decrypt(found.api_key_enc) == "fake-key-gamma"

        # Once the old key is dropped the re-hashed key still works.
        monkeypatch.setattr(crypto, "_ring", KeyRing(new))
        assert identity_main._find_user_for_api_key(session, "fake-key-gamma") is not None


def test_homehub_store_reads_values_sealed_with_the_previous_key(tmp_path, keys):
    old, new = keys
    from services.homehub.store import Store

    before = Store(str(tmp_path / "hub.db"), old.decode())
    before.save_blob("certs", {"made": "up"})
    before.close()
    during = Store(str(tmp_path / "hub.db"), new.decode(), old.decode())
    assert during.load_blob("certs") == {"made": "up"}
    during.close()
    with pytest.raises(InvalidToken):
        Store(str(tmp_path / "hub.db"), new.decode()).load_blob("certs")
