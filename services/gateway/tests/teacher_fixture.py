"""The shared secret fixture (``tools/opencode-trainer-sync/test/fixtures``), built.

The plugin's node tests read the same file, so the Python scrubber and the
plugin path are held to one list of things that must never survive.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tools" / "opencode-trainer-sync" / "test" / "fixtures" / "secret_cases.json"


def _join(pieces: list[list[Any]]) -> str:
    return "".join(str(text) * int(times) for text, times in pieces)


def load() -> dict[str, Any]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    secrets = {}
    for case in data["secrets"]:
        value = _join(case["pieces"])
        secrets[case["kind"]] = {"value": value, "text": case.get("context", "{}").replace("{}", value)}
    env_lines = [str(line[0]) + _join(line[1:]) for line in data["env_file_lines"]]
    return {"names": data["names"], "secrets": secrets, "env_lines": env_lines,
            "session": data["decrypted_keys_session"]}


def fill(template: str, secrets: dict[str, dict[str, str]]) -> str:
    for kind, item in secrets.items():
        template = template.replace("{" + kind + "}", item["value"])
    return template
