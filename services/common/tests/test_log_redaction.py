import logging

from services.common.logging_utils import redact_access_log, redact_query_secrets


def test_credentials_in_a_url_are_masked_and_the_rest_is_kept():
    line = "POST /api/webhook/git-pull/x?token=abc123&branch=main HTTP/1.1"
    assert redact_query_secrets(line) == "POST /api/webhook/git-pull/x?token=***&branch=main HTTP/1.1"
    assert redact_query_secrets("/api/media/events?user=a&mt=SECRET") == "/api/media/events?user=a&mt=***"
    assert redact_query_secrets("/api/users?limit=5") == "/api/users?limit=5"


def test_uvicorns_access_log_never_carries_a_token(caplog):
    redact_access_log()
    redact_access_log()  # twice is harmless
    access = logging.getLogger("uvicorn.access")
    assert sum(type(f).__name__ == "_RedactQuerySecrets" for f in access.filters) == 1
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        access.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", "/workspaces?token=hunter2", "1.1", 200)
    assert "hunter2" not in caplog.text and "token=***" in caplog.text
