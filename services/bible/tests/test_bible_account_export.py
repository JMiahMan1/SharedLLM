"""The Bible service's part of the account export (docs/ACCOUNT_EXPORT.md)."""
from datetime import UTC, datetime

from sqlmodel import Session, select

from services.bible.models import PlanEnrollment, StudySession, StudySessionTurn, VerseMark


def _seed(engine):
    t = datetime(2026, 1, 2, 8, 0, tzinfo=UTC)
    with Session(engine) as s:
        s.add(VerseMark(username="ruth", osis="John", chapter=3, verse_start=16, kind="highlight", color="gold", created_at=t, updated_at=t))
        s.add(VerseMark(username="boaz", osis="Ruth", chapter=1, verse_start=16, kind="highlight", created_at=t, updated_at=t))
        s.add(PlanEnrollment(username="ruth", plan_id="psalms-30", started_on="2026-01-01", completed_days="1,2", group_id=7))
        study = StudySession(username="ruth", title="John 3", reference="John 3", created_at=t, updated_at=t)
        s.add(study)
        s.commit()
        s.refresh(study)
        s.add(StudySessionTurn(session_id=study.id, question="Who is Nicodemus?", answer="A Pharisee."))
        s.commit()


def test_needs_the_internal_secret(client, engine):
    assert client.get("/internal/account/export/ruth", headers={"X-Internal-Secret": "wrong"}).status_code == 403


def test_export_holds_only_that_reader(client, engine):
    _seed(engine)
    data = client.get("/internal/account/export/ruth").json()["collections"]
    assert [m["osis"] for m in data["marks"]] == ["John"]
    assert data["study_session_turns"][0]["question"] == "Who is Nicodemus?"
    assert data["plan_enrollments"][0]["completed_days"] == "1,2"


def test_restore_to_an_empty_account_and_again_is_idempotent(client, engine):
    _seed(engine)
    data = client.get("/internal/account/export/ruth").json()["collections"]
    # The same reader on a new server: nothing there yet.
    out = client.post("/internal/account/import/naomi", json={"collections": data, "dry_run": False}).json()["collections"]
    assert out["marks"]["add"] == 1
    assert out["study_sessions"]["add"] == 1
    assert out["study_session_turns"]["add"] == 1
    again = client.post("/internal/account/import/naomi", json={"collections": data, "dry_run": True}).json()["collections"]
    bad = {k: c for k, c in again.items() if c["add"] or c["conflict"]}
    assert not bad, bad
    with Session(engine) as s:
        mine = s.exec(select(StudySession).where(StudySession.username == "naomi")).one()
        assert s.exec(select(StudySessionTurn).where(StudySessionTurn.session_id == mine.id)).one().answer == "A Pharisee."
        enrolment = s.exec(select(PlanEnrollment).where(PlanEnrollment.username == "naomi")).one()
        assert enrolment.group_id is None  # never put into someone's group
        assert len(s.exec(select(VerseMark).where(VerseMark.username == "ruth")).all()) == 1


def test_an_older_highlight_does_not_replace_a_newer_one(client, engine):
    _seed(engine)
    data = client.get("/internal/account/export/ruth").json()["collections"]
    data["marks"][0]["color"] = "blue"
    data["marks"][0]["updated_at"] = "2025-12-01T00:00:00+00:00"
    out = client.post("/internal/account/import/ruth", json={"collections": data, "dry_run": False}).json()["collections"]
    assert out["marks"]["conflict"] == 1
    with Session(engine) as s:
        assert s.exec(select(VerseMark).where(VerseMark.username == "ruth")).one().color == "gold"
