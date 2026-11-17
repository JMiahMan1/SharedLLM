"""Health's part of the account export (docs/ACCOUNT_EXPORT.md)."""
from sqlmodel import Session, select

from services.health.models import Checkin, Cheer, HealthProfile, HealthShare, MetricSample


def _seed(engine):
    with Session(engine) as s:
        s.add(HealthProfile(user_id="alex", display_name="Alex", kid_mode=True, updated_at="2026-01-01T00:00:00+00:00"))
        s.add(HealthProfile(user_id="blair", display_name="Blair"))
        s.add(MetricSample(user_id="alex", metric="weight", value=70, unit="kg", recorded_at="2026-01-02", source="manual", external_id="w1"))
        s.add(MetricSample(user_id="blair", metric="weight", value=99, unit="kg", recorded_at="2026-01-02", source="manual", external_id="w1"))
        s.add(Checkin(user_id="alex", day="2026-01-02", updated_at="2026-01-02T10:00:00+00:00"))
        s.add(HealthShare(owner="alex", viewer="blair", scope="weight"))
        s.add(Cheer(from_user="blair", to_user="alex", day="2026-01-02", kind="steps"))
        s.commit()


def test_needs_the_internal_secret(client, engine):
    _seed(engine)
    assert client.get("/internal/account/export/alex", headers={"X-Internal-Secret": "nope"}).status_code == 403


def test_export_holds_only_that_person(client, engine):
    _seed(engine)
    data = client.get("/internal/account/export/alex").json()["collections"]
    assert [m["value"] for m in data["metrics"]] == [70]
    assert data["profile"][0]["user_id"] == "alex"
    assert data["cheers_received"][0]["from_user"] == "blair"
    assert data["shares_given"][0]["viewer"] == "blair"
    assert "Blair" not in [p["display_name"] for p in data["profile"]]


def test_restore_merges_by_natural_key_and_never_reaches_another_person(client, engine):
    _seed(engine)
    data = client.get("/internal/account/export/alex").json()["collections"]
    data["metrics"].append({"id": 999, "user_id": "blair", "metric": "weight", "value": 71, "unit": "kg",
                            "recorded_at": "2026-01-03", "source": "manual", "external_id": "w2"})
    data["profile"][0].update(display_name="Alex B", kid_mode=False, updated_at="2026-03-01T00:00:00+00:00")
    data["checkins"][0]["updated_at"] = "2025-01-01T00:00:00+00:00"  # older than the server's
    preview = client.post("/internal/account/import/alex", json={"collections": data, "dry_run": True}).json()["collections"]
    assert preview["metrics"] == {"add": 1, "update": 0, "conflict": 0, "unchanged": 1, "skipped": 0, "errors": []}
    assert preview["profile"]["update"] == 1
    assert preview["shares_given"]["skipped"] == 1
    out = client.post("/internal/account/import/alex", json={"collections": data, "dry_run": False}).json()["collections"]
    assert out["metrics"]["add"] == 1
    with Session(engine) as s:
        alex = s.get(HealthProfile, "alex")
        assert alex.display_name == "Alex B"
        assert alex.kid_mode is True  # a parent's switch is never restored
        blair = s.exec(select(MetricSample).where(MetricSample.user_id == "blair")).all()
        assert [m.value for m in blair] == [99]
        assert len(s.exec(select(MetricSample).where(MetricSample.user_id == "alex")).all()) == 2
        assert len(s.exec(select(HealthShare)).all()) == 1


def test_restore_twice_changes_nothing(client, engine):
    _seed(engine)
    data = client.get("/internal/account/export/alex").json()["collections"]
    client.post("/internal/account/import/alex", json={"collections": data, "dry_run": False})
    again = client.post("/internal/account/import/alex", json={"collections": data, "dry_run": True}).json()["collections"]
    for name, counts in again.items():
        assert counts["add"] == 0 and counts["update"] == 0 and counts["conflict"] == 0, name
