"""scripts/dispatch_mission.py reads a mission file without running it."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "dispatch_mission.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("dispatch_mission", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


dm = _load_module()


@pytest.mark.parametrize("mission", sorted((ROOT / "missions").glob("*.py")))
def test_the_shipped_missions_load(mission):
    spec = dm.load_mission_spec(str(mission))
    assert spec["query"]
    assert spec["slug"]


def test_a_mission_file_is_parsed_never_executed(tmp_path):
    marker = tmp_path / "ran"
    mission = tmp_path / "evil.py"
    mission.write_text(
        f"open({str(marker)!r}, 'w').write('x')\n"
        "MISSION_SPEC = {'query': 'build it', 'slug': 'it'}\n"
    )
    assert dm.load_mission_spec(str(mission)) == {"query": "build it", "slug": "it"}
    assert not marker.exists()


def test_a_computed_spec_is_refused(tmp_path):
    mission = tmp_path / "computed.py"
    mission.write_text("import os\nMISSION_SPEC = {'query': os.getenv('Q')}\n")
    with pytest.raises(dm.MissionFileError, match="literal dict"):
        dm.load_mission_spec(str(mission))


def test_a_file_without_a_spec_is_refused(tmp_path):
    mission = tmp_path / "empty.py"
    mission.write_text("X = 1\n")
    with pytest.raises(dm.MissionFileError, match="MISSION_SPEC"):
        dm.load_mission_spec(str(mission))


def test_json_missions_load_and_other_files_are_refused(tmp_path):
    good = tmp_path / "m.json"
    good.write_text('{"query": "q", "slug": "s"}')
    assert dm.load_mission_spec(str(good)) == {"query": "q", "slug": "s"}
    other = tmp_path / "m.yaml"
    other.write_text("query: q\n")
    with pytest.raises(dm.MissionFileError, match=r"\.json or \.py"):
        dm.load_mission_spec(str(other))


def test_priority_help_matches_the_queue_scale():
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=True
    ).stdout
    out = " ".join(out.split())
    assert "1=high" not in out
    assert "Higher is more urgent" in out
