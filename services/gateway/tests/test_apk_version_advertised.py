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
            files={"apk": ("jarvis-os.apk", make_apk_bytes(version_code, version_name), "application/vnd.android.package-archive")},
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

    def test_advertises_a_digest_the_client_can_verify(self, client):
        import hashlib

        from services.gateway import main

        self._publish_apk(client, 22)
        data = client.get("/api/app-updates/version").json()
        assert data["apk_sha256"] == hashlib.sha256(
            (main.APP_UPDATES_DIR / "jarvis-os.apk").read_bytes()
        ).hexdigest()

    def test_withholds_an_apk_it_cannot_hash(self, client, monkeypatch):
        """No digest means we cannot authenticate the download, so we do not
        offer it -- an unverifiable APK is worse than none."""
        from services.gateway import main

        self._publish_apk(client, 22)
        monkeypatch.setattr(main, "read_apk_digest", lambda _p: None)
        main.clear_apk_version_cache()
        data = client.get("/api/app-updates/version").json()
        assert data["apk_available"] is False
        assert data["apk_sha256"] is None
        assert data["apk_version_code"] is None

    def test_reports_no_digest_when_no_apk_is_published(self, client):
        data = client.get("/api/app-updates/version").json()
        assert data["apk_sha256"] is None

    def test_reports_no_version_code_when_no_apk_is_published(self, client):
        data = client.get("/api/app-updates/version").json()
        assert data["apk_available"] is False
        # None, never a placeholder -- the client treats a missing code as
        # "cannot tell" rather than "up to date".
        assert data["apk_version_code"] is None
        assert data["apk_url"] is None

    def test_withholds_an_apk_whose_version_cannot_be_read(self, client):
        from services.gateway import main

        (main.APP_UPDATES_DIR / "jarvis-os.apk").write_bytes(b"not a zip")
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


class TestApkServing:
    """The published artifact is the release build, not a debug build."""

    def _publish(self, client, code=22):
        r = client.post(
            "/api/app-updates/publish",
            headers=auth(),
            files={"apk": ("anything.apk", make_apk_bytes(code), "application/vnd.android.package-archive")},
        )
        assert r.status_code == 200

    def test_serves_the_apk_under_its_real_name(self, client):
        self._publish(client)
        r = client.get("/api/app-updates/jarvis-os.apk")
        assert r.status_code == 200
        assert r.headers["content-disposition"] == "attachment; filename=jarvis-os.apk"

    def test_advertises_the_real_name_to_clients(self, client):
        self._publish(client)
        url = client.get("/api/app-updates/version").json()["apk_url"]
        assert url.endswith("/api/app-updates/jarvis-os.apk")

    def test_stores_the_apk_under_its_real_name(self, client):
        from services.gateway import main

        self._publish(client)
        assert (main.APP_UPDATES_DIR / "jarvis-os.apk").exists()
        # The misleading name must not linger on disk alongside it.
        assert not (main.APP_UPDATES_DIR / "app-debug.apk").exists()

    def test_the_old_debug_path_redirects_rather_than_404ing(self, client):
        """A stale client or bookmark must not look like a broken update."""
        self._publish(client)
        r = client.get("/api/app-updates/app-debug.apk", follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "/api/app-updates/jarvis-os.apk"

    def test_404s_when_no_apk_is_published(self, client):
        assert client.get("/api/app-updates/jarvis-os.apk").status_code == 404


class TestPublishEndpoint:
    def test_records_the_uploaded_apk_version(self, client):
        r = client.post(
            "/api/app-updates/publish",
            headers=auth(),
            files={"apk": ("jarvis-os.apk", make_apk_bytes(23, "1.5.0"), "application/vnd.android.package-archive")},
        )
        assert r.status_code == 200
        assert r.json()["metadata"]["apk_version_code"] == 23

    def test_prefers_the_artifact_over_a_claimed_code(self, client):
        r = client.post(
            "/api/app-updates/publish",
            headers=auth(),
            data={"apk_version_code": "99"},
            files={"apk": ("jarvis-os.apk", make_apk_bytes(23), "application/vnd.android.package-archive")},
        )
        assert r.status_code == 200
        assert r.json()["metadata"]["apk_version_code"] == 23

    def test_refuses_an_apk_it_cannot_read(self, client):
        """Publishing an unreadable APK would advertise a version we cannot
        honour, which is the failure mode being fixed."""
        r = client.post(
            "/api/app-updates/publish",
            headers=auth(),
            files={"apk": ("jarvis-os.apk", b"not a zip at all", "application/vnd.android.package-archive")},
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
            files={"apk": ("jarvis-os.apk", good, "application/vnd.android.package-archive")},
        )
        assert r.status_code == 200

        bad = client.post(
            "/api/app-updates/publish",
            headers=auth(),
            files={"apk": ("jarvis-os.apk", b"truncated garbage", "application/vnd.android.package-archive")},
        )
        assert bad.status_code == 400

        served = (main.APP_UPDATES_DIR / "jarvis-os.apk").read_bytes()
        assert served == good
        # And the endpoint still describes what it is actually serving.
        assert client.get("/api/app-updates/version").json()["apk_version_code"] == 22

    def test_requires_the_internal_secret(self, client):
        r = client.post(
            "/api/app-updates/publish",
            files={"apk": ("jarvis-os.apk", make_apk_bytes(23), "application/vnd.android.package-archive")},
        )
        assert r.status_code == 403
