"""Phase 7: the photo journal and spoken-name resolution.

Photos: everyone may add one (it is the family's journal); a thumbnail is served
from local storage; deleting is the photographer's or an adult's. Resolution:
a personal name beats a species, a species beats a room, and a tie is reported
as ambiguous with the candidates rather than guessed.
"""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden.main import app  # noqa: E402
from scripts.build_plants_seed import build_seed  # noqa: E402

#: A 1x1 JPEG, the smallest real image a phone could send as a thumbnail.
TINY_JPEG = base64.b64encode(
    bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300ffd9"
    )
).decode()


def _headers(user: str, role: str) -> dict[str, str]:
    return {
        "X-Internal-Secret": INTERNAL_SECRET,
        "X-User-Id": user,
        "X-User-Role": role,
        "X-Is-Admin": "true" if role == "admin" else "false",
        "X-Family-Manage": "true" if role in ("mother", "father") else "false",
    }


PARENT = _headers("mother", "mother")
ADULT = _headers("kaleb", "adult_child")
CHILD = _headers("noah", "child")


@pytest.fixture()
def tmp_seed_path(tmp_path):
    seed_path = tmp_path / "plants-seed.db"
    build_seed(seed_path)
    return str(seed_path)


@pytest.fixture()
def client(tmp_seed_path, tmp_path, monkeypatch):
    import services.garden.main as main_module

    main_module.app.state.engine = None
    main_module._SEED = None
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{tmp_path / 'garden.db'}")
    monkeypatch.setenv("PLANT_SEED_PATH", tmp_seed_path)
    monkeypatch.setenv("GARDEN_THUMB_DIR", str(tmp_path / "thumbs"))
    engine = main_module._engine()
    SQLModel.metadata.create_all(engine)
    main_module.app.state.engine = engine
    return TestClient(app)


@pytest.fixture()
def session(client):
    import services.garden.main as main_module

    with Session(main_module.app.state.engine) as db:
        yield db


def _plant(client, *, nickname="Monty", species_ref="monstera_deliciosa", category="houseplant",
           location_id=None, assigned_to=None) -> dict:
    body = {"category": category, "species_ref": species_ref}
    if nickname:
        body["nickname"] = nickname
    if location_id:
        body["location_id"] = location_id
    if assigned_to:
        body["assigned_to"] = assigned_to
    resp = client.post("/plants", headers=PARENT, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _room(client, name="Living Room") -> dict:
    resp = client.post("/locations", headers=PARENT, json={"name": name, "kind": "raised_bed"})
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------- the photo journal

def test_a_child_may_add_a_progress_photo(client):
    plant = _plant(client)

    resp = client.post(
        f"/plants/{plant['id']}/photos",
        headers=CHILD,
        json={"kind": "progress", "notes": "New leaf!", "thumb_base64": TINY_JPEG},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "progress"
    assert body["taken_by"] == "noah"
    assert body["notes"] == "New leaf!"
    assert body["thumb_path"].endswith(".jpg")


def test_the_thumbnail_is_served_from_local_storage(client):
    plant = _plant(client)
    photo = client.post(
        f"/plants/{plant['id']}/photos", headers=CHILD, json={"thumb_base64": TINY_JPEG}
    ).json()

    resp = client.get(f"/plants/{plant['id']}/photos/{photo['id']}/thumb", headers=CHILD)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")
    assert resp.content.startswith(b"\xff\xd8")


def test_a_photo_without_a_thumbnail_says_404_for_the_thumb(client):
    plant = _plant(client)
    photo = client.post(f"/plants/{plant['id']}/photos", headers=CHILD, json={}).json()

    assert client.get(f"/plants/{plant['id']}/photos/{photo['id']}/thumb", headers=CHILD).status_code == 404


def test_a_diagnosis_photo_keeps_its_structured_summary(client):
    plant = _plant(client)

    resp = client.post(
        f"/plants/{plant['id']}/photos",
        headers=ADULT,
        json={
            "kind": "diagnosis",
            "ai_summary_json": '{"urgency": "medium", "summary": "Overwatered."}',
        },
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "diagnosis"
    assert '"urgency": "medium"' in resp.json()["ai_summary_json"]


def test_an_unknown_kind_is_refused(client):
    plant = _plant(client)

    resp = client.post(f"/plants/{plant['id']}/photos", headers=CHILD, json={"kind": "selfie"})

    assert resp.status_code == 400
    assert "kind" in resp.json()["detail"]


def test_a_thumbnail_that_is_not_an_image_is_refused(client):
    plant = _plant(client)
    not_an_image = base64.b64encode(b"hello there").decode()

    resp = client.post(
        f"/plants/{plant['id']}/photos", headers=CHILD, json={"thumb_base64": not_an_image}
    )

    assert resp.status_code == 400
    assert "JPEG or PNG" in resp.json()["detail"]


def test_photos_are_listed_newest_first(client):
    plant = _plant(client)
    for taken in ("2026-11-01", "2026-11-15", "2026-11-10"):
        client.post(
            f"/plants/{plant['id']}/photos",
            headers=CHILD,
            json={"taken_at": f"{taken}T09:00:00+00:00"},
        )

    photos = client.get(f"/plants/{plant['id']}/photos", headers=CHILD).json()["photos"]

    assert [p["taken_at"][:10] for p in photos] == ["2026-11-15", "2026-11-10", "2026-11-01"]


def test_a_photo_on_an_unknown_plant_is_404(client):
    assert client.get("/plants/nope/photos", headers=CHILD).status_code == 404
    assert client.post("/plants/nope/photos", headers=CHILD, json={}).status_code == 404


def test_a_child_may_delete_their_own_photo(client):
    plant = _plant(client)
    photo = client.post(f"/plants/{plant['id']}/photos", headers=CHILD, json={}).json()

    assert client.delete(f"/plants/{plant['id']}/photos/{photo['id']}", headers=CHILD).status_code == 204
    assert client.get(f"/plants/{plant['id']}/photos", headers=CHILD).json()["count"] == 0


def test_a_child_cannot_delete_someone_elses_photo(client):
    plant = _plant(client)
    photo = client.post(f"/plants/{plant['id']}/photos", headers=ADULT, json={}).json()

    assert client.delete(f"/plants/{plant['id']}/photos/{photo['id']}", headers=CHILD).status_code == 403
    assert client.delete(f"/plants/{plant['id']}/photos/{photo['id']}", headers=PARENT).status_code == 204


def test_deleting_a_photo_removes_its_thumbnail_file(client, session):
    from services.garden.main import _thumbs_dir

    plant = _plant(client)
    photo = client.post(
        f"/plants/{plant['id']}/photos", headers=CHILD, json={"thumb_base64": TINY_JPEG}
    ).json()
    thumb = _thumbs_dir() / photo["thumb_path"]
    assert thumb.is_file()

    client.delete(f"/plants/{plant['id']}/photos/{photo['id']}", headers=CHILD)

    assert not thumb.exists()


# --------------------------------------------------------------------------- resolving a spoken name

def test_a_personal_name_resolves_to_its_plant(client):
    plant = _plant(client, nickname="Monty")

    body = client.get("/plants/resolve", headers=CHILD, params={"q": "Monty"}).json()

    assert body["match_type"] == "plant"
    assert body["plants"][0]["plant_id"] == plant["id"]
    assert body["plants"][0]["nickname"] == "Monty"


def test_the_family_name_beats_the_species(client):
    _plant(client, nickname="Monty")
    other = _plant(client, nickname="Sir Basil", species_ref="basil_sweet", category="herb")

    body = client.get("/plants/resolve", headers=CHILD, params={"q": "sir basil"}).json()

    assert body["match_type"] == "plant"
    assert body["plants"][0]["plant_id"] == other["id"]


def test_a_species_name_finds_every_plant_of_it(client):
    first = _plant(client, nickname="Monty")
    second = _plant(client, nickname="Monty Two")

    body = client.get("/plants/resolve", headers=CHILD, params={"q": "monstera"}).json()

    ids = {row["plant_id"] for row in body["plants"]}
    assert ids == {first["id"], second["id"]}
    assert body["match_type"] == "ambiguous"


def test_a_spoken_plural_matches_the_species(client):
    tomato = _plant(client, nickname="Roma Row", species_ref="tomato_roma", category="vegetable")

    body = client.get("/plants/resolve", headers=CHILD, params={"q": "tomatoes"}).json()

    assert body["match_type"] == "plant"
    assert body["plants"][0]["plant_id"] == tomato["id"]


def test_a_room_resolves_when_no_plant_does(client):
    room = _room(client, "Living Room")

    body = client.get("/plants/resolve", headers=CHILD, params={"q": "living room"}).json()

    assert body["match_type"] == "location"
    assert body["location"]["location_id"] == room["id"]


def test_a_room_phrase_still_finds_the_room(client):
    _room(client, "Kitchen")

    body = client.get("/plants/resolve", headers=CHILD, params={"q": "kitchen herbs"}).json()

    assert body["match_type"] == "location"
    assert body["location"]["name"] == "Kitchen"


def test_an_unknown_name_is_none_not_a_guess(client):
    _plant(client)

    body = client.get("/plants/resolve", headers=CHILD, params={"q": "audrey two"}).json()

    assert body["match_type"] == "none"
    assert body["plants"] == []
    assert body["location"] is None


def test_an_empty_name_is_refused(client):
    assert client.get("/plants/resolve", headers=CHILD, params={"q": "the"}).status_code == 400


def test_resolution_needs_a_person(client):
    assert client.get("/plants/resolve", headers={"X-Internal-Secret": INTERNAL_SECRET}, params={"q": "monty"}).status_code == 401


def test_resolution_carries_what_a_caller_needs_to_speak(client):
    plant = _plant(client, nickname="Monty", assigned_to="noah")

    row = client.get("/plants/resolve", headers=CHILD, params={"q": "monty"}).json()["plants"][0]

    assert row["plant_id"] == plant["id"]
    assert "Monstera" in row["common_name"]
    assert row["category"] == "houseplant"
    assert row["assigned_to"] == "noah"


def test_batch_watering_records_where_it_came_from(client, session):
    from services.garden.models import CareLog

    room = _room(client, "Living Room")
    _plant(client, nickname="Monty", location_id=room["id"])

    resp = client.post(
        f"/locations/{room['id']}/water-all",
        headers=CHILD,
        params={"source": "voice", "due_only": "false"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["watered_count"] == 1
    logs = session.query(CareLog).filter(CareLog.action == "water").all()
    assert [row.source for row in logs] == ["voice"]
