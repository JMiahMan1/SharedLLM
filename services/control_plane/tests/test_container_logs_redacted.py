"""The Admin log viewer's container logs never show a credential. Every value here is made up."""
from services.control_plane import main


class _Container:
    name = "sharedllm_workspace_runtime"

    def logs(self, **_kw):
        return (b'INFO: "POST /api/webhook/git-pull/x?token=abcdef123456 HTTP/1.1" 200\n'
                b"sent own-secret-value-42 upstream\n")


def test_container_logs_are_masked(monkeypatch):
    monkeypatch.setattr(main, "client", object())
    monkeypatch.setattr(main, "_resolve_container", lambda _name: _Container())
    monkeypatch.setattr(main, "_verify_sharedllm_container", lambda _c: True)
    monkeypatch.setattr(main, "KNOWN_SECRETS", ("own-secret-value-42",))
    out = main.get_container_logs("workspace_runtime", tail=10)
    assert "abcdef123456" not in out["logs"] and "own-secret-value-42" not in out["logs"]
    assert "POST /api/webhook/git-pull/x?token=***" in out["logs"]
