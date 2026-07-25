"""Tests for the workspace terminal WebSocket endpoint."""

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

# Must be set before importing workspace_runtime modules
os.environ["INTERNAL_SECRET"] = "test-secret"

import contextlib

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
from starlette.websockets import WebSocketDisconnect

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(name="db_file", scope="session")
def _db_file():
    """Session-scoped file-based SQLite so every engine shares the same DB."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    yield "sqlite:///" + tmp.name
    with contextlib.suppress(OSError):
        os.unlink(tmp.name)


@pytest.fixture(name="db_engine")
def _db_engine(db_file):
    """Engine that creates tables then returns them."""
    engine = create_engine(db_file, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(autouse=True)
def _patch_engine(db_engine):
    """Replace the workspace_runtime database engine with our test engine."""
    from services.workspace_runtime import database as db_mod

    original = db_mod.engine
    db_mod.engine = db_engine
    yield
    db_mod.engine = original


@pytest.fixture(name="client")
def _client():
    """TestClient with patched engine."""
    from services.workspace_runtime.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_docker_api(
    resize_ok: bool = True,
    kill_ok: bool = True,
    exec_id: str = "exec-123",
):
    """Return a MagicMock simulating docker_client.api."""
    api = MagicMock()
    api.exec_create = MagicMock(return_value={"Id": exec_id})

    mock_sock = MagicMock()
    # Return empty bytes by default so read_socket() exits on first recv (EOF)
    mock_sock.recv = MagicMock(return_value=b"")
    # Use same socket instance for every exec_start call
    api.exec_start = MagicMock(return_value=mock_sock)

    if resize_ok:
        api.exec_resize = MagicMock()
    else:
        api.exec_resize = MagicMock(side_effect=Exception("resize failed"))

    if kill_ok:
        api.exec_kill = MagicMock()
    else:
        api.exec_kill = MagicMock(side_effect=Exception("kill failed"))

    return api


def _make_mock_docker_client(container_status: str = "running"):
    """Build a fully mocked docker client with a running container."""
    mock_api = _make_mock_docker_api()

    mock_container = MagicMock()
    mock_container.status = container_status


    mock_client = MagicMock()
    mock_client.api = mock_api
    mock_client.containers.get.return_value = mock_container
    return mock_client


def _setup_test_workspace(client, workspace_id: str = "ws1", local_path: str = "/tmp/ws1"):
    """Create a test workspace in the DB and return its data."""
    ws_data = {
        "id": workspace_id,
        "display_name": "Test Workspace",
        "local_path": local_path,
        "sync_mode": "git",
        "scope": "user",
    }
    resp = client.post(
        "/workspaces",
        json=ws_data,
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert resp.status_code == 200, f"Failed to create workspace: {resp.text}"
    return resp.json()


async def _mock_auth_response(status: int = 200, json_data: dict | None = None):
    """Create an awaitable that returns a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data if json_data is not None else {})
    return resp


def _mock_auth_context(username: str = "test", is_admin: bool = False, status: int = 200):
    """Return a context manager that patches get_client() for auth.

    The real endpoint does:
        async with get_client() as client:
            resp = await client.get(url, ...)
    We return a mock wrapper whose *method* 'get' is an async function.
    """
    from services.workspace_runtime import main as main_mod

    async def mock_get(*args, **kwargs):
        return await _mock_auth_response(status, {"username": username, "is_admin": is_admin})

    mock_wrapper = MagicMock()
    mock_wrapper.get = mock_get
    mock_wrapper.__aenter__ = AsyncMock(return_value=mock_wrapper)
    mock_wrapper.__aexit__ = AsyncMock(return_value=None)

    return patch.object(main_mod, "get_client", return_value=mock_wrapper)


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


def test_terminal_rejects_missing_token(client):
    """No token → close(1008, 'Missing authentication token')."""
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect("/ws/workspace/ws1/terminal"):
        pass
    assert exc.value.code == 1008
    assert "Missing authentication token" in exc.value.reason


def test_terminal_rejects_invalid_token(client):
    """Invalid token (401) → close(1008, 'Invalid token')."""

    async def mock_auth_get(*_args, **_kwargs):
        resp = MagicMock()
        resp.status = 401
        return resp

    mock_wrapper = MagicMock()
    mock_wrapper.get = mock_auth_get
    mock_wrapper.__aenter__ = AsyncMock(return_value=mock_wrapper)
    mock_wrapper.__aexit__ = AsyncMock(return_value=None)

    from services.workspace_runtime import main as main_mod

    with patch.object(main_mod, "get_client", return_value=mock_wrapper), pytest.raises(
        WebSocketDisconnect
    ) as exc, client.websocket_connect("/ws/workspace/ws1/terminal?token=bad"):
        pass
    assert exc.value.code == 1008
    assert "Invalid token" in exc.value.reason


def test_terminal_auth_service_unavailable(client):
    """Auth timeout → close(1011, 'Auth service unavailable')."""
    from services.workspace_runtime import main as main_mod

    mock_wrapper = MagicMock()
    mock_wrapper.get = AsyncMock(side_effect=TimeoutError("timeout"))
    mock_wrapper.__aenter__ = AsyncMock(return_value=mock_wrapper)
    mock_wrapper.__aexit__ = AsyncMock(return_value=None)

    with patch.object(main_mod, "get_client", return_value=mock_wrapper), pytest.raises(
        WebSocketDisconnect
    ) as exc, client.websocket_connect("/ws/workspace/ws1/terminal?token=ok"):
        pass
    assert exc.value.code == 1011
    assert "Auth service unavailable" in exc.value.reason


# ---------------------------------------------------------------------------
# Docker / container tests
# ---------------------------------------------------------------------------


def test_terminal_docker_unavailable(client, caplog):
    """Docker daemon down → close(1011, 'Docker unavailable')."""
    _setup_test_workspace(client)

    with _mock_auth_context(), patch("docker.from_env", side_effect=Exception("no daemon")):
        with client.websocket_connect("/ws/workspace/ws1/terminal?token=ok"):
            pass
    assert "Docker unavailable" in caplog.text


def test_terminal_container_not_found(client, caplog):
    """Container doesn't exist → close(1011, 'Container unavailable')."""
    import docker

    _setup_test_workspace(client)

    with _mock_auth_context(), patch(
        "docker.from_env",
        side_effect=docker.errors.NotFound("not found", message="No such container"),
    ), client.websocket_connect("/ws/workspace/ws1/terminal?token=ok"):
        pass
    assert "Container unavailable" in caplog.text


# ---------------------------------------------------------------------------
# Exec create tests
# ---------------------------------------------------------------------------


def test_terminal_creates_exec_for_bash(client, caplog):
    """exec_create is called; bash is preferred."""
    _setup_test_workspace(client)

    mock_client = _make_mock_docker_client()

    with _mock_auth_context(), patch(
        "docker.from_env", return_value=mock_client
    ), patch("services.workspace_sandbox.ensure_workspace_container"), client.websocket_connect("/ws/workspace/ws1/terminal?token=ok"):
        pass

    assert mock_client.api.exec_create.call_count >= 1


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


def test_terminal_forwards_websocket_input_to_exec(client, caplog):
    """Text input from WebSocket is forwarded to exec stdin."""
    _setup_test_workspace(client)

    mock_client = _make_mock_docker_client()

    # Track sendall calls
    sendall_calls = []

    def capture_sendall(data):
        sendall_calls.append(data)

    mock_sock = mock_client.api.exec_start.return_value
    mock_sock.sendall = MagicMock(side_effect=capture_sendall)

    with _mock_auth_context(), patch(
        "docker.from_env", return_value=mock_client
    ), patch("services.workspace_sandbox.ensure_workspace_container"), client.websocket_connect("/ws/workspace/ws1/terminal?token=ok") as ws:
        ws.send_text("ls -la")
        import time

        time.sleep(0.2)

    # Verify sendall was called with the input
    assert len(sendall_calls) > 0
    assert b"ls -la" in sendall_calls[0]


# ---------------------------------------------------------------------------
# Resize tests
# ---------------------------------------------------------------------------


def test_terminal_resize_calls_exec_resize(client):
    """Resize control message triggers exec_resize."""
    _setup_test_workspace(client)

    with _mock_auth_context():
        mock_client = _make_mock_docker_client()

        with patch(
            "docker.from_env", return_value=mock_client
        ), patch("services.workspace_sandbox.ensure_workspace_container"), pytest.raises(
            WebSocketDisconnect
        ), client.websocket_connect("/ws/workspace/ws1/terminal?token=ok") as ws:
            ws.send_text(json.dumps({"type": "resize", "width": 120, "height": 40}))
            import time

            time.sleep(0.2)

        mock_client.api.exec_resize.assert_called_with("exec-123", width=120, height=40)


def test_terminal_resize_failure_doesnt_crash(client):
    """Failed exec_resize is logged but doesn't crash the terminal."""
    _setup_test_workspace(client)

    with _mock_auth_context():
        mock_client = _make_mock_docker_client()
        mock_client.api.exec_resize = MagicMock(side_effect=Exception("resize failed"))

        with patch(
            "docker.from_env", return_value=mock_client
        ), patch("services.workspace_sandbox.ensure_workspace_container"), pytest.raises(
            WebSocketDisconnect
        ), client.websocket_connect("/ws/workspace/ws1/terminal?token=ok") as ws:
            ws.send_text(json.dumps({"type": "resize", "width": 120, "height": 40}))
            import time

            time.sleep(0.2)

        mock_client.api.exec_resize.assert_called_once()


# ---------------------------------------------------------------------------
# Shutdown tests
# ---------------------------------------------------------------------------


def test_terminal_exec_kill_on_close(client):
    """WebSocket close triggers exec_kill(signal=15)."""
    _setup_test_workspace(client)

    with _mock_auth_context():
        mock_client = _make_mock_docker_client()

        with patch(
            "docker.from_env", return_value=mock_client
        ), patch("services.workspace_sandbox.ensure_workspace_container"), pytest.raises(
            WebSocketDisconnect
        ), client.websocket_connect("/ws/workspace/ws1/terminal?token=ok") as ws:
            import time

            time.sleep(0.2)
            ws.close()

        mock_client.api.exec_kill.assert_called_with("exec-123", signal=15)


def test_terminal_socket_close_on_error(client):
    """Socket errors during streaming don't crash."""
    _setup_test_workspace(client)

    with _mock_auth_context():
        mock_client = _make_mock_docker_client()

        mock_sock = mock_client.api.exec_start.return_value
        mock_sock.recv = MagicMock(side_effect=Exception("connection reset"))

        with patch(
            "docker.from_env", return_value=mock_client
        ), patch("services.workspace_sandbox.ensure_workspace_container"), pytest.raises(
            WebSocketDisconnect
        ), client.websocket_connect("/ws/workspace/ws1/terminal?token=ok"):
            pass

        mock_client.api.exec_kill.assert_called()
