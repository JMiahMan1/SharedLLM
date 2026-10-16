"""What the gateway forwards to the logging service."""
from services.gateway import main


def test_routine_polls_are_quiet_when_they_succeed():
    for path in ("/health/ready", "/api/users/michele/location", "/api/telemetry/notifications/wait",
                 "/api/users/location", "/api/music/songs/abc123"):
        assert main._is_routine_poll(path), path


def test_real_requests_are_forwarded():
    for path in ("/api/users/me", "/api/geo/trips", "/api/bible/daily", "/api/users/michele/avatar/image"):
        assert not main._is_routine_poll(path), path
