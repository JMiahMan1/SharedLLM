import os
import sys
import tempfile
import pytest
from cryptography.fernet import Fernet

_test_fernet_key = Fernet.generate_key().decode()

# Ensure root is in PYTHONPATH for imports across services
_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

os.environ.setdefault("INTERNAL_SECRET", "test-secret-ci")
os.environ["FERNET_KEY"] = _test_fernet_key
os.environ.setdefault("INIT_DB", "false")
os.environ.setdefault("WORKSPACE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("IDENTITY_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("IDENTITY_SVC_URL", "http://localhost:8001")
os.environ.setdefault("EXECUTION_SVC_URL", "http://localhost:8003")
os.environ.setdefault("RAG_SVC_URL", "http://localhost:8003")
os.environ.setdefault("STORAGE_SVC_URL", "http://localhost:8005")
os.environ.setdefault("LOGGING_SVC_URL", "http://localhost:8006")
os.environ.setdefault("WORKSPACE_RUNTIME_SVC_URL", "http://localhost:8007")
os.environ.setdefault("CONTROL_PLANE_URL", "http://localhost:8008")
os.environ.setdefault("SEARXNG_URL", "http://localhost:8080")
os.environ.setdefault("LLAMA_SERVER_PROXY_URL", "http://localhost:8009")
os.environ.setdefault("FAST_PATH_THRESHOLD", "0.85")
os.environ.setdefault("EMBEDDING_MODEL", "nomic-embed-text-v1.5")
os.environ.setdefault("TEST_MODE", "true")


@pytest.fixture(scope="session")
def test_fernet_key():
    return _test_fernet_key


@pytest.fixture(scope="session")
def redis_container():
    try:
        from testcontainers.redis import RedisContainer  # pyright: ignore[reportMissingImports]
        with RedisContainer("redis:7-alpine") as redis:
            yield redis
    except Exception:
        pytest.skip("Docker not available for testcontainers")


@pytest.fixture(scope="function")
def redis_client(redis_container):
    import redis
    client = redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=redis_container.get_exposed_port(6379),
        decode_responses=True,
    )
    client.flushall()
    yield client
    client.flushall()
    client.close()


@pytest.fixture(scope="function")
def identity_db_session():
    from sqlmodel import SQLModel, create_engine, Session
    engine = create_engine("sqlite:///:memory:")
    import services.identity.models  # noqa: F401 - registers models with SQLModel.metadata  # pyright: ignore[reportUnusedImport]
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def temp_storage_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture(scope="function")
def temp_chroma_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def pytest_configure(config):
    config.addinivalue_line("markers", "local_only: requires local running servers (--run-local to enable)")
    config.addinivalue_line("markers", "server_only: requires remote running servers (--run-server to enable)")
    config.addinivalue_line("markers", "integration: tests that verify inter-service communication")
    config.addinivalue_line("markers", "contract: tests that validate service-to-service API contracts")
    config.addinivalue_line("markers", "unit: pure logic tests with no I/O")


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
