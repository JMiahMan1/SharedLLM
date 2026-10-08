"""``index_capabilities`` must fail loudly instead of reporting a false success.

The script is triggered by ``POST /execute/index_capabilities``, whose route
treats exit code 0 as success. The script used to catch every sync failure
and log it, so a rejected or timed-out POST still exited 0 and the route
answered ``"Capabilities re-indexed successfully."`` while the RAG collection
never changed. The live symptom was CalibreRequest missing from
``system_capabilities`` behind a success response and an empty ``output``.

These tests read the script and route from disk rather than importing them:
the script logs to stderr and imports execution-side config, and the route
lives in the gateway's execution app. The pins keep the re-raise, the HTTP
status check, the client timeout that survives a slow embed, and the stderr
``log`` field the route now returns so a successful run is observable.
"""

from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "index_capabilities.py"
).read_text()
ROUTE = (Path(__file__).resolve().parents[1] / "execution" / "main.py").read_text()


def test_a_connection_failure_is_reraised_instead_of_swallowed():
    assert "except Exception as e:" in SCRIPT
    assert 'log.error(f"Capability sync failed: {e}")' in SCRIPT
    handler = SCRIPT.split('log.error(f"Capability sync failed: {e}")', 1)[1]
    assert handler.lstrip().startswith("raise"), (
        "without the re-raise the script exits 0 and the route reports "
        "success for a sync that never landed"
    )


def test_a_rejected_sync_raises_instead_of_just_logging():
    assert "raise RuntimeError(" in SCRIPT
    assert "RAG rejected the capability sync" in SCRIPT
    assert 'log.error(f"Failed to index capabilities:' not in SCRIPT, (
        "logging a non-200 and falling through to exit 0 is the same lie"
    )


def test_the_post_timeout_survives_a_slow_embed():
    assert "timeout=300" in SCRIPT, (
        "embedding ~35 capability schemas can exceed the old 30s client "
        "timeout, which reports failure while the server is still working"
    )


def test_a_successful_run_returns_its_log_not_just_empty_stdout():
    assert '"log": result.stderr[-4000:]' in ROUTE, (
        "the script logs to stderr, so a success response without the log "
        "field shows an empty output and no way to see what ran"
    )
