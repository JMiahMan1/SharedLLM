import os
import sys
import pytest
from pathlib import Path
from cryptography.fernet import Fernet

# Generate a fresh Fernet key for each test run — never hardcode secrets
_test_fernet_key = Fernet.generate_key().decode()

# Set test environment variables BEFORE any module imports
os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ["FERNET_KEY"] = _test_fernet_key
os.environ.setdefault("INIT_DB", "false")
os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("IDENTITY_SVC_URL", "http://localhost:8001")
os.environ.setdefault("EXECUTION_SVC_URL", "http://localhost:8002")
os.environ.setdefault("RAG_SVC_URL", "http://localhost:8003")
os.environ.setdefault("SEARXNG_URL", "http://localhost:8080")

sys.path.insert(0, str(Path(__file__).parent / "services"))


@pytest.fixture(scope="session")
def test_fernet_key():
    """Provide the dynamically generated Fernet key for this test run."""
    return _test_fernet_key


def pytest_configure(config):
    config.addinivalue_line("markers", "local_only: requires local running servers")
    config.addinivalue_line("markers", "server_only: requires remote running servers")


def pytest_collection_modifyitems(config, items):
    skip_local = pytest.mark.skip(reason="Skipping: requires local running servers (--run-local to enable)")
    skip_server = pytest.mark.skip(reason="Skipping: requires remote running servers (--run-server to enable)")

    run_local = config.getoption("--run-local", default=False)
    run_server = config.getoption("--run-server", default=False)

    for item in items:
        if "local_only" in item.keywords and not run_local:
            item.add_marker(skip_local)
        if "server_only" in item.keywords and not run_server:
            item.add_marker(skip_server)


def pytest_addoption(parser):
    parser.addoption("--run-local", action="store_true", default=False, help="Run tests requiring local running servers")
    parser.addoption("--run-server", action="store_true", default=False, help="Run tests requiring remote running servers")
