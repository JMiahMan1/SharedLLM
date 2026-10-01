"""The advertised APK version must describe the APK actually being served.

Regression tests for a real bug: ``apk_version_code`` was parsed out of
``build.gradle`` on every bundle deploy, so the advertised number drifted
ahead of the published artifact (server said 23, the APK on disk was 22) and
a user who installed the newest APK on offer was told to update forever.
"""
import io
import sys
import zipfile
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from services.gateway.tests.test_apk_version_manifest import build_manifest


def make_apk_bytes(version_code: int, version_name: str = "1.4.12") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("AndroidManifest.xml", build_manifest(version_code, version_name))
    return buf.getvalue()


@pytest.fixture(name="client")
def client_fixture(monkeypatch, tmp_path):
    sys.modules["fastembed"] = MagicMock()
    mock_engine = MagicMock()
    mock_engine.engine = MagicMock()
    mock_engine.engine.classify.return_value = ("unknown", 0.0)
    mock_engine.engine.should_bypass_llm.return_value = False
    sys.modules["intent_engine"] = mock_engine
    sys.modules["background_worker"] = MagicMock()

    from services.gateway import main
    from services.gateway.apk_manifest import clear_cache

    updates = tmp_path / "app_updates"
    updates.mkdir()
    monkeypatch.setattr(main, "APP_UPDATES_DIR", updates)
    main.background_tasks = None  # pyright: ignore[reportAttributeAccessIssue]
    clear_cache()
    yield TestClient(main.app)
    clear_cache()


def auth():
    """The publish endpoint is internal-secret protected."""
    from services.gateway.main import INTERNAL_SECRET

    return {"X-Internal-Secret": INTERNAL_SECRET}


class TestVersionEndpoint:
    def _publish_apk(self, client, version_code, version_name="1.4.12"):
        r = client.post(
            "/api/app-updates/publish",
            headers=auth(),
            files={"apk": ("app-debug.apk", make_apk_bytes(version_code, version_name), "application/vnd.android.package-archive")},
        )
        assert r.status_code == 200, r.text

    def test_advertises_the_version_of_the_apk_actually_served(self, client):
        self._publish_apk(client, 22)
        data = client.get("/api/app-updates/version").json()
        assert data["apk_available"] is True
        assert data["apk_version_code"] == 22
        assert data["apk_version_name"] == "1.4.12"

    def test_ignores_a_stale_claimed_version_code(self, client):
        """Metadata claiming 23 while the artifact is 22 must not win."""
        self._publish_apk(client, 22)
        # Simulate the old drift: metadata on disk disagrees with the artifact.
        import json

        from services.gateway import main

        meta = json.loads((main.APP_UPDATES_DIR / "version.json").read_text())
        meta["apk_version_code"] = 23
        (main.APP_UPDATES_DIR / "version.json").write_text(json.dumps(meta))

        data = client.get("/api/app-updates/version").json()
        assert data["apk_version_code"] == 22

    def test_reports_no_version_code_when_no_apk_is_published(self, client):
        data = client.get("/api/app-updates/version").json()
        assert data["apk_available"] is False
        # None, never a placeholder -- the client treats a missing code as
        # "cannot tell" rather than "up to date".
        assert data["apk_version_code"] is None
        assert data["apk_url"] is None

    def test_withholds_an_apk_whose_version_cannot_be_read(self, client):
        from services.gateway import main

        (main.APP_UPDATES_DIR / "app-debug.apk").write_bytes(b"not a zip")
        data = client.get("/api/app-updates/version").json()
        assert data["apk_available"] is False
        assert data["apk_version_code"] is None

    def test_a_bundle_only_publish_keeps_the_installed_apk_version(self, client):
        """Deploying a web bundle must not invent a new native version."""
        self._publish_apk(client, 22)
        r = client.post("/api/app-updates/publish", headers=auth(), data={"version": "1.5.0", "git_sha": "abc1234"})
        assert r.status_code == 200
        data = client.get("/api/app-updates/version").json()
        assert data["apk_version_code"] == 22


class TestPublishEndpoint:
    def test_records_the_uploaded_apk_version(self, client):
        r = client.post(
            "/api/app-updates/publish",
            headers=auth(),
            files={"apk": ("app-debug.apk", make_apk_bytes(23, "1.5.0"), "application/vnd.android.package-archive")},
        )
        assert r.status_code == 200
        assert r.json()["metadata"]["apk_version_code"] == 23

    def test_prefers_the_artifact_over_a_claimed_code(self, client):
        r = client.post(
            "/api/app-updates/publish",
            headers=auth(),
            data={"apk_version_code": "99"},
            files={"apk": ("app-debug.apk", make_apk_bytes(23), "application/vnd.android.package-archive")},
        )
        assert r.status_code == 200
        assert r.json()["metadata"]["apk_version_code"] == 23

    def test_refuses_an_apk_it_cannot_read(self, client):
        """Publishing an unreadable APK would advertise a version we cannot
        honour, which is the failure mode being fixed."""
        r = client.post(
            "/api/app-updates/publish",
            headers=auth(),
            files={"apk": ("app-debug.apk", b"not a zip at all", "application/vnd.android.package-archive")},
        )
        assert r.status_code == 400
        assert "unreadable" in r.json()["detail"].lower()

    def test_a_refused_upload_does_not_destroy_the_live_apk(self, client):
        """A corrupt CI upload must not take the served build down with it."""
        from services.gateway import main

        good = make_apk_bytes(22)
        r = client.post(
            "/api/app-updates/publish",
            headers=auth(),
            files={"apk": ("app-debug.apk", good, "application/vnd.android.package-archive")},
        )
        assert r.status_code == 200

        bad = client.post(
            "/api/app-updates/publish",
            headers=auth(),
            files={"apk": ("app-debug.apk", b"truncated garbage", "application/vnd.android.package-archive")},
        )
        assert bad.status_code == 400

        served = (main.APP_UPDATES_DIR / "app-debug.apk").read_bytes()
        assert served == good
        # And the endpoint still describes what it is actually serving.
        assert client.get("/api/app-updates/version").json()["apk_version_code"] == 22

    def test_requires_the_internal_secret(self, client):
        r = client.post(
            "/api/app-updates/publish",
            files={"apk": ("app-debug.apk", make_apk_bytes(23), "application/vnd.android.package-archive")},
        )
        assert r.status_code == 403
