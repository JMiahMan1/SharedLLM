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


# -- redact_secrets: what the Admin log viewer shows. Every value here is made up. --

import pytest  # noqa: E402

from services.common.logging_utils import redact_secrets, secret_env_values  # noqa: E402


@pytest.mark.parametrize("line, secret", [
    ("GET /api/webhook/git-pull/x?token=abcdef123456 HTTP/1.1", "abcdef123456"),
    ('{"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJlMTIz"}', "eyJhbGciOiJIUzI1NiJ9"),
    ("X-Internal-Secret: made-up-secret-value", "made-up-secret-value"),
    ("login password=correcthorsebattery ok", "correcthorsebattery"),
    ('"api_key": "abcd1234efgh5678"', "abcd1234efgh5678"),
    ("PrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=", "yAnz5TF+lXXJte14"),
    ("PresharedKey = Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA==", "Zm9vYmFyYmF6"),
    ("cookie: session=deadbeefcafe0011; path=/", "deadbeefcafe0011"),
    ("pushed with ghp_abcdefghijklmnopqrstuvwxyz0123", "ghp_abcdefghijklmnop"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----", "b3BlbnNzaC1rZXk"),
])
def test_credentials_are_masked(line, secret):
    out = redact_secrets(line)
    assert secret not in out and "***" in out


def test_ordinary_lines_stay_readable():
    for line in ("used 512 tokens, token: 512", "a secret meeting at noon", "GET /api/rooms?area=den 200"):
        assert redact_secrets(line) == line


def test_the_readers_own_secrets_are_masked_wherever_they_appear():
    assert redact_secrets("sent made-up-value-123 to the hub", known=("made-up-value-123",)) == "sent *** to the hub"


def test_secret_env_values_takes_only_secret_looking_names():
    env = {"INTERNAL_SECRET": "x" * 12, "GH_TOKEN": "y" * 20, "HOME": "/home/someone", "NC_PASSWORD": "short"}
    assert secret_env_values(env) == ("y" * 20, "x" * 12)
