import os
import sys
import tempfile

import pytest
from cryptography.fernet import Fernet

_test_fernet_key = Fernet.generate_key().decode()

# Load env files (.env.test takes precedence over .env) for any var the explicit
# test defaults below do not cover. Called BEFORE them, so the pins always win.
def _load_env_files():
    for _env_name in (".env.test", ".env"):
        _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _env_name)
        if not os.path.exists(_env_path):
            continue
        with open(_env_path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _v = _line.split("=", 1)
                _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
                os.environ.setdefault(_k, _v)


# Ensure root is in PYTHONPATH for imports across services
_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

os.environ["FERNET_KEY"] = _test_fernet_key
os.environ.setdefault("INIT_DB", "false")
os.environ.setdefault("WORKSPACE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("IDENTITY_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BIBLE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BLB_BASE_URL", "https://bible.test")
os.environ.setdefault("BIBLE_DEVOTIONAL_DIR", os.path.join(tempfile.gettempdir(), "bible_devotionals"))
os.environ.setdefault("CALIBRE_LIBRARY_PATH", "")
os.environ.setdefault("BIBLE_IMPORT_DIR", os.path.join(tempfile.gettempdir(), "bible_imports"))
os.environ.setdefault("BIBLE_API_KEY", "")
os.environ.setdefault("BIBLE_PROVIDER_CACHE", os.path.join(tempfile.gettempdir(), "bible_provider_cache"))
os.environ.setdefault("BIBLE_PROVIDER_CALL_BUDGET", "1200")
os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("IDENTITY_SVC_URL", "http://localhost:8001")
# Network-mode service URLs (required by config._net_url)
# NETWORK_MODE defaults to "bridge" (lowercase), so keys are bridge_* not BRIDGE_*
os.environ.setdefault("bridge_IDENTITY_SVC_URL", "http://localhost:8001")
os.environ.setdefault("bridge_EXECUTION_SVC_URL", "http://localhost:8003")
os.environ.setdefault("bridge_RAG_SVC_URL", "http://localhost:8004")
os.environ.setdefault("bridge_STORAGE_SVC_URL", "http://localhost:8005")
os.environ.setdefault("bridge_LOGGING_SVC_URL", "http://localhost:8006")
os.environ.setdefault("bridge_WORKSPACE_RUNTIME_SVC_URL", "http://localhost:8007")
os.environ.setdefault("bridge_GEO_SVC_URL", "http://localhost:8009")
os.environ.setdefault("bridge_BIBLE_SVC_URL", "http://localhost:8010")
os.environ.setdefault("HOST_IDENTITY_SVC_URL", "http://127.0.0.1:8001")
os.environ.setdefault("EXECUTION_SVC_URL", "http://localhost:8003")
os.environ.setdefault("RAG_SVC_URL", "http://localhost:8004")
os.environ.setdefault("STORAGE_SVC_URL", "http://localhost:8005")
os.environ.setdefault("LOGGING_SVC_URL", "http://localhost:8006")
os.environ.setdefault("WORKSPACE_RUNTIME_SVC_URL", "http://localhost:8007")
os.environ.setdefault("CONTROL_PLANE_URL", "http://localhost:8008")
os.environ.setdefault("SEARXNG_URL", "http://localhost:8080")
os.environ.setdefault("LLAMA_SERVER_PROXY_URL", "http://localhost:8009")
os.environ.setdefault("FAST_PATH_THRESHOLD", "0.85")
os.environ.setdefault("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
os.environ.setdefault("TEST_MODE", "true")

# Every env var services/config.py reads must have a deterministic test default.
#
# Without one, a var that is only set in a developer's real .env silently
# differs between their machine and CI, and services/config.py derives each of
# them ONCE at import into module attributes that ~30 modules capture. A test
# that then reloads services.config (or another module imports it later) reads a
# *different* snapshot than the module under test, which is how
# test_abs_stream_url.py came to compare 192.168.2.205 against localhost: a
# single assertion failure that said nothing about the code it meant to cover.
#
# Pinning also stops _load_env_files() from injecting real deployment secrets
# (ABS_API_KEY, HA_TOKEN, the Git tokens) into the test environment.
#
# tests/unit/test_config_env_isolation.py fails if this ever falls behind.
_TEST_ENV_DEFAULTS = {
    # --- The values a test MUST see exactly, whatever the runner exported ---
    #
    # INTERNAL_SECRET had a module-scope assignment in two dozen test files and
    # whichever imported first won: the gateway captures it once at import, so a
    # test that sent a different literal in its X-Internal-Secret header got a
    # 401 that had nothing to do with the code it meant to cover (five
    # test_music_proxy tests, all green in isolation).
    # tests/unit/test_workspace_env_enc.py had already worked around this for
    # itself with a per-test fixture; the fix belongs here.
    "INTERNAL_SECRET": "test-secret",
    "FERNET_KEY": _test_fernet_key,
    # Service topology — localhost everywhere, matching CI, never the docker
    # service aliases (which only resolve inside the compose network).
    "GATEWAY_INTERNAL_URL": "http://gateway:11435",
    "EXECUTION_EXTERNAL_HOST": "localhost",
    # Jarvis's public address, handed to paired devices. Blank in tests: the
    # pairing tests set it themselves, and the rest must not depend on a
    # developer's .env.
    "JARVIS_HOST": "",
    "NETWORK_MODE": "bridge",
    "REDIS_URL": "redis://localhost:6379/0",
    "SEARXNG_URL": "http://localhost:8080",
    "SCRIPTS_DIR": "/app/scripts",
    "MODELS_DIR": "/app/models",
    "COMPOSE_PROJECT_DIR": "/app",
    "MASS_CONFIG_ENTRY_ID": "1",
    # Models — a named local model, so a test that resolves "which model"
    # gets a stable answer instead of whatever the developer last deployed.
    "DEFAULT_MODEL": "test-model:q4_k_m",
    "ASSISTANT_MODEL": "test-model:q4_k_m",
    "CODING_MODEL": "test-model:q4_k_m",
    "LIBRARIAN_MODEL": "test-model:q4_k_m",
    "DEFAULT_TTS_VOICE": "af_heart",
    # Third-party integrations — dummies. The live URLs and tokens belong in
    # .env, never in a test run.
    "HA_URL": "http://localhost:8123",
    "OSRM_URL": "",
    "OSRM_FOOT_URL": "",
    "NOMINATIM_URL": "",
    "HA_TOKEN": "test-ha-token",
    "ABS_URL": "http://localhost:13378/",
    "ABS_API_KEY": "test-abs-key",
    "AUDIOBOOKSHELF_URL": "http://localhost:13378",
    "AUDIOBOOKSHELF_USER": "test-user",
    "AUDIOBOOKSHELF_PASS": "test-pass",
    "NEXTCLOUD_URL": "http://localhost:8081",
    "NEXTCLOUD_USER": "test-user",
    "NEXTCLOUD_PASS": "test-pass",
    "GITHUB_URL": "https://github.com",
    "GITHUB_USER": "test-user",
    "GITHUB_TOKEN": "test-github-token",
    "GITLAB_URL": "https://gitlab.com",
    "GITLAB_USER": "test-user",
    "GITLAB_TOKEN": "test-gitlab-token",
    "GIT_URL": "https://github.com/test/repo.git",
    "GIT_USER": "test-user",
    "GIT_TOKEN": "test-git-token",
    "GIT_WEBHOOK_SECRET": "test-webhook-secret",
    "UPSTREAM_DNS": "127.0.0.1",
    "DNS_CONF_PATH": "/etc/dnsmasq.conf",
    # Filesystem — tmp paths, so a test can never write into a real deployment.
    "WORKSPACE_ROOT": tempfile.gettempdir(),
    "WORKSPACE_REGISTRY_PATH": os.path.join(tempfile.gettempdir(), "workspaces.json"),
    "LOCAL_NOTES_ROOT": os.path.join(tempfile.gettempdir(), "notes"),
    "PHRASEBOOK_PATH": os.path.join(_root, "data", "phrasebook.json"),
    "CHROMA_PERSIST_DIR": os.path.join(tempfile.gettempdir(), "chroma_db"),
    "VOLUME_BACKUP_ROOT": os.path.join(tempfile.gettempdir(), "backups"),
    "VOLUME_MANIFEST_PATH": os.path.join(tempfile.gettempdir(), "volumes.json"),
    "LEGACY_ENV_PATH": os.path.join(_root, ".env"),
    "TEMP_MEDIA_DIR": os.path.join(tempfile.gettempdir(), "sharedllm_media"),
    # Device registry — an in-memory store so a test can never read or write the
    # real device list. Set at import time by two execution test modules before
    # conftest's isolation fixture existed.
    "DEVICE_REGISTRY_PATH": ":memory:",
    # --- Gateway-only configuration ---
    #
    # services/gateway/config.py is a SECOND config module (separate from
    # services/config.py) and it also derives its constants once, at import.
    # ALPACA_AUDIO_URL is the one that bit: test_music_proxy.py set it with a
    # module-scope setdefault, which runs at *collection*. Run that file alone
    # and the setdefault lands before services.gateway.main is imported, so the
    # route saw the URL; run the whole suite and some earlier module had already
    # imported main, ALPACA_AUDIO_URL was captured as "", and four tests failed
    # with a 503 that read like a missing-configuration error. Pinning here
    # moves the value ahead of every import, which is the only ordering that
    # cannot lose a race.
    "ALPACA_AUDIO_URL": "http://audio.test:8082",
    "EMBEDDING_MODEL": "nomic-ai/nomic-embed-text-v1.5",
    # Identity seeding requires a password and refuses to boot without one.
    "DEFAULT_ADMIN_PASSWORD": "test-admin-password",
    # Mail — dummies. The real relay credentials live in .env.
    "MAIL_URL": "http://localhost:8025",
    "MAIL_ADMIN_URL": "http://localhost:8025/admin",
    "MAIL_ADMIN": "test-admin",
    "MAIL_PASS": "test-mail-pass",
    "MAIL_USER": "test-user",
    "MAIL_USER_PASS": "test-user-pass",
    # Alpaca dashboard, which owns the podcast mixer and the /api/podcast/*
    # routes the execution handlers call. (ALPACA_AUDIO_URL above is the
    # audio-server itself — speaker identification needs OpenVoice's reference
    # encoder, and only that container has torch.)
    "ALPACA_WEB_URL": "http://web.test:5000",
    # Presentation
    "TIMEZONE": "UTC",
    "ANNOUNCEMENT_BLACKLIST": "test-announcement",
}

# Load real env config (.env.test > .env) first, then pin on top of it.
#
# Order AND operator both matter, and getting either wrong is invisible:
#
#  * Pinning first with setdefault looked correct and was not. It defers to
#    whatever is already in the environment, so a CI `env:` block or a developer's
#    exported shell variable overrode every "deterministic" default — which is
#    exactly how five test_music_proxy tests kept returning 401 against a real
#    INTERNAL_SECRET while services/config.py had captured a different one.
#  * Pinning first with a plain assignment would let _load_env_files() overwrite
#    the dummies with the developer's real ABS_API_KEY / HA_TOKEN / Git tokens
#    moments later.
#
# So: load, then ASSIGN. After this block, every var above holds its listed
# value no matter what the runner or the developer's .env contained.
#
# The opt-out exists for `local_only` runs that genuinely point at live
# infrastructure; it is never set in CI.
_PASSTHROUGH = os.environ.get("SHAREDLLM_TEST_PASSTHROUGH_ENV", "").strip().lower() in {"1", "true", "yes"}
if not _PASSTHROUGH:
    for _key, _val in _TEST_ENV_DEFAULTS.items():
        os.environ[_key] = _val

# Load real env config (.env.test > .env) for any var not pinned above.
_load_env_files()

if _PASSTHROUGH:
    # Honour the real deployment for the vars a live run needs, but keep the
    # values that would otherwise silently point tests at production storage.
    for _key, _val in _TEST_ENV_DEFAULTS.items():
        os.environ.setdefault(_key, _val)


@pytest.fixture(autouse=True)
def _isolate_environ():
    """Stop one test's os.environ edits from becoming the next test's baseline.

    Several tests reload services.config, which re-derives every constant from
    the *current* environment. Combined with the env clobbering some test
    modules do at import time, an un-restored environment makes a large part of
    the suite order-dependent: the same test passes alone and fails in a full
    run, or the reverse. Snapshot and restore so each test starts clean.
    """
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


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
    from sqlmodel import Session, SQLModel, create_engine
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
