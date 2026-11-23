"""
Tests for non-blocking image pull, pull status, pull-and-restart, and
volume permission handling in the Control Plane service.
"""

import os

import pytest


@pytest.fixture
def control_plane_code():
    """Load the control_plane main.py code."""
    with open(
        os.path.join(os.path.dirname(__file__), "..", "main.py"),
    ) as f:
        return f.read()


class TestNonBlockingPull:
    """Test that image pull is non-blocking with background thread tracking."""

    def test_pull_status_tracking_globals_exist(self, control_plane_code):
        """Global pull status dict and lock should be defined."""
        assert "_pull_status" in control_plane_code
        assert "_pull_lock" in control_plane_code
        assert "threading.Lock()" in control_plane_code

    def test_background_pull_function_exists(self, control_plane_code):
        """_pull_image_background function should exist."""
        assert "def _pull_image_background" in control_plane_code
        assert "threading.Thread" in control_plane_code
        assert "daemon=True" in control_plane_code

    def test_pull_endpoint_starts_background_thread(self, control_plane_code):
        """POST /api/containers/{service_name}/pull should start a background thread."""
        assert 'def pull_image_update' in control_plane_code
        assert "thread.start()" in control_plane_code
        assert '"status": "pulling"' in control_plane_code or "'status': 'pulling'" in control_plane_code

    def test_pull_endpoint_checks_existing_pull(self, control_plane_code):
        """Pull endpoint should check if a pull is already in progress."""
        assert "Pull already in progress" in control_plane_code

    def test_pull_status_endpoint_exists(self, control_plane_code):
        """GET /api/containers/{service_name}/pull/status should exist."""
        assert "def get_pull_status" in control_plane_code
        assert "/api/containers/{service_name}/pull/status" in control_plane_code


class TestPullAndRestart:
    """Test the combined pull-and-restart (phone-update) endpoint."""

    def test_pull_and_restart_endpoint_exists(self, control_plane_code):
        """POST /api/containers/{service_name}/pull-and-restart should exist."""
        assert "def pull_and_restart" in control_plane_code
        assert "/api/containers/{service_name}/pull-and-restart" in control_plane_code

    def test_pull_and_restart_calls_recreate(self, control_plane_code):
        """Pull-and-restart should call _recreate_container."""
        assert "_recreate_container" in control_plane_code

    def test_pull_and_restart_checks_image_change(self, control_plane_code):
        """Pull-and-restart should check if the image ID changed."""
        assert "new_image_id == current_image_id" in control_plane_code or "new_image_id != current_image_id" in control_plane_code

    def test_pull_and_restart_returns_updated_flag(self, control_plane_code):
        """Pull-and-restart should return an 'updated' flag."""
        assert '"updated"' in control_plane_code or "'updated'" in control_plane_code


class TestPermissionsHandling:
    """Test volume permission handling that mirrors deploy.sh."""

    def test_fix_volume_permissions_function_exists(self, control_plane_code):
        """_fix_volume_permissions function should exist."""
        assert "def _fix_volume_permissions" in control_plane_code

    def test_fix_volume_permissions_uses_puid_pgid(self, control_plane_code):
        """Should use os.getuid() and os.getgid() for permission fixing."""
        assert "os.getuid()" in control_plane_code
        assert "os.getgid()" in control_plane_code

    def test_fix_volume_permissions_chown(self, control_plane_code):
        """Should run chown -R on volumes."""
        assert "chown -R" in control_plane_code

    def test_fix_volume_permissions_chmod(self, control_plane_code):
        """Should run chmod -R on volumes."""
        assert "chmod -R" in control_plane_code

    def test_recreate_calls_permission_fix(self, control_plane_code):
        """_recreate_container fixes volume permissions before the new container starts."""
        recreate_pos = control_plane_code.find("def _recreate_container")
        call_pos = control_plane_code.find("fixed_vols = _fix_volume_permissions(container)", recreate_pos)
        assert call_pos > recreate_pos, "_fix_volume_permissions should be called within _recreate_container"
        assert "prepare=_prepare" in control_plane_code[recreate_pos:]


class TestRecreateNetworkSafety:
    """Recreate must snapshot config while healthy and survive self-update.

    The behaviour (networks at create, carry-over, health gate, rollback) is
    driven against a fake Docker in test_recreate.py.
    """

    def test_snapshot_taken_before_anything_changes(self, control_plane_code):
        recreate_pos = control_plane_code.find("def _recreate_container")
        body = control_plane_code[recreate_pos:]
        snap_at = body.find("snap = _snapshot_container_config(container)")
        swap_at = body.find("recreate_from_snapshot(")
        assert -1 < snap_at < swap_at

    def test_self_recreate_runs_outside_this_container(self, control_plane_code):
        """Self-recreate must not stop this process in-place (exit 137 left CP down),
        and a child process here would die with the container, so a helper container runs it."""
        assert "_is_self_container" in control_plane_code
        assert "_recreate_self_detached" in control_plane_code
        assert "control_plane_updater_" in control_plane_code
        assert "run_detached_self_recreate" in control_plane_code

    def test_a_refused_update_is_a_409(self, control_plane_code):
        assert control_plane_code.count("except _recreate.RecreateFailed") == 2


class TestUpdateDetectionImprovements:
    """Test improvements to the update detection logic."""

    def test_ghcr_auth_ok_no_longer_has_bug(self, control_plane_code):
        """The _ghcr_auth_ok function should not have the e.code in (200,) bug."""
        assert "e.code in (200,)" not in control_plane_code

    def test_docs_mention_github_token_fallback(self):
        """Docs should mention GITHUB_TOKEN as a fallback."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "CONTROL_PLANE_SERVICE.md"),
        ) as f:
            docs = f.read()
        assert "GITHUB_TOKEN" in docs

    def test_env_example_has_ghcr_token(self):
        """.env.example should document GHCR_TOKEN."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env.example"),
        ) as f:
            env = f.read()
        assert "GHCR_TOKEN" in env

    def test_env_example_has_github_token(self):
        """.env.example should document GITHUB_TOKEN."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env.example"),
        ) as f:
            env = f.read()
        assert "GITHUB_TOKEN" in env


class TestPullSurvivesADeadConnection:
    """dockerd closes an idle kept-alive connection; the next pull must not fail on it."""

    def _main(self, monkeypatch, pulls):
        import http.client

        import services.control_plane.main as m

        calls = {"n": 0, "closed": 0}

        class Api:
            def pull(self, tag, stream, decode):
                calls["n"] += 1
                outcome = pulls[calls["n"] - 1]
                if isinstance(outcome, Exception):
                    raise outcome
                return iter(outcome)

            def close(self):
                calls["closed"] += 1

        class Client:
            api = Api()

        monkeypatch.setattr(m, "client", Client())
        monkeypatch.setattr(m.time, "sleep", lambda s: None)
        return m, calls, http.client

    def test_a_dropped_connection_is_retried_on_a_new_one(self, monkeypatch):
        import http.client

        dead = http.client.RemoteDisconnected("Remote end closed connection without response")
        m, calls, _ = self._main(monkeypatch, [dead, [{"status": "Downloaded newer image"}]])
        seen = []
        m._pull_streaming("ghcr.io/example/app:latest", seen.append)
        assert calls == {"n": 2, "closed": 1}
        assert seen == [{"status": "Downloaded newer image"}]

    def test_a_connection_that_never_comes_back_still_fails(self, monkeypatch):
        import requests

        err = requests.exceptions.ConnectionError("gone")
        m, calls, _ = self._main(monkeypatch, [err, err, err])
        with pytest.raises(requests.exceptions.ConnectionError):
            m._pull_streaming("ghcr.io/example/app:latest")
        assert calls["n"] == m.PULL_ATTEMPTS

    def test_an_answer_from_docker_is_not_retried(self, monkeypatch):
        from docker.errors import APIError

        m, calls, _ = self._main(monkeypatch, [APIError("manifest unknown")])
        with pytest.raises(APIError):
            m._pull_streaming("ghcr.io/example/app:nope")
        assert calls["n"] == 1
