import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

# Ensure the package root is importable
sys.path.insert(0, ".")

from services.workspace_runtime import main as rt


def test_created_at_in_config_tz_converts_utc_to_config_tz():
    # Stored UTC naive -> emitted as offset-aware ISO in configured tz.
    with patch.object(rt, "get_config_timezone", return_value="America/Phoenix"):
        # 2026-07-18T00:00:00Z should become 2026-07-17T17:00:00-07:00
        utc = datetime(2026, 7, 18, 0, 0, 0, tzinfo=UTC)
        out = rt._created_at_in_config_tz(utc)
        assert out is not None
        assert out.endswith("-07:00")
        assert "2026-07-17T17:00:00" in out


def test_created_at_naive_treated_as_utc():
    with patch.object(rt, "get_config_timezone", return_value="America/Phoenix"):
        naive = datetime(2026, 7, 18, 0, 0, 0)  # no tzinfo -> assumed UTC
        out = rt._created_at_in_config_tz(naive)
        assert out is not None
        assert "2026-07-17T17:00:00" in out


def test_created_at_none_returns_none():
    with patch.object(rt, "get_config_timezone", return_value="UTC"):
        assert rt._created_at_in_config_tz(None) is None


def test_workspace_to_dict_emits_config_tz_created_at():
    with patch.object(rt, "get_config_timezone", return_value="America/Phoenix"):
        ws = MagicMock()
        ws.model_dump.return_value = {"id": "t1", "display_name": "T"}
        ws.webhook_token_enc = None
        ws.env_enc = None
        ws.excludes = []
        ws.created_at = datetime(2026, 7, 18, 0, 0, 0, tzinfo=UTC)
        d = rt._workspace_to_dict(ws)
        assert d["created_at"].endswith("-07:00")
        assert "2026-07-17T17:00:00" in d["created_at"]
