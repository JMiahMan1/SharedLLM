import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "test" / "fixtures" / "code_helper_bug_example"
API_URL = os.getenv("API_URL", "http://localhost:11435")
VOICE_ID = os.getenv("VOICE_ID", "default")
MODEL = os.getenv("CODE_HELPER_MODEL", "qwen2.5-coder:7b")
TIMEOUT = int(os.getenv("CODE_HELPER_TIMEOUT", "180"))


def load_fixture_text() -> tuple[str, str]:
    source = (FIXTURE_DIR / "math_utils.py").read_text()
    tests = (FIXTURE_DIR / "test_math_utils.py").read_text()
    return source, tests


def build_prompt(source: str, tests: str) -> str:
    return f"""Fix this Python code bug in `math_utils.py`.

This is a coding task, not a device or media command.

Return only the corrected contents of `math_utils.py` in a single fenced Python code block.
Do not include explanation, diff markers, or any extra files.

The current `math_utils.py` is:
```python
{source}
```

The test file is:
```python
{tests}
```

Requirements:
- Keep the public function names the same.
- Fix only what is necessary for the tests to pass.
- Preserve the current behavior of `normalize_username`.
"""


def extract_python_code(response_text: str) -> str | None:
    fenced = re.findall(r"```python\s*(.*?)```", response_text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced[0].strip() + "\n"

    generic = re.findall(r"```\s*(.*?)```", response_text, flags=re.DOTALL)
    if generic:
        return generic[0].strip() + "\n"
    return None


def run_gateway_query(prompt: str) -> dict:
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "voice_id": VOICE_ID,
        "model": MODEL,
    }
    resp = requests.post(f"{API_URL}/api/chat", json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def run_pytest_with_candidate(candidate_source: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix="sharedllm-code-eval-") as tmpdir:
        tmp = Path(tmpdir)
        shutil.copy(FIXTURE_DIR / "test_math_utils.py", tmp / "test_math_utils.py")
        (tmp / "math_utils.py").write_text(candidate_source)

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(tmp / "test_math_utils.py")],
            cwd=tmp,
            capture_output=True,
            text=True,
            check=False,
        )
        output = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
        return proc.returncode, output.strip()


def main() -> int:
    source, tests = load_fixture_text()
    prompt = build_prompt(source, tests)

    print("=== SharedLLM Code Helper Live Eval ===")
    print(f"Gateway: {API_URL}/api/chat")
    print(f"Fixture: {FIXTURE_DIR}")
    print(f"Requested model: {MODEL}")

    try:
        response = run_gateway_query(prompt)
    except Exception as exc:
        print(f"[FAIL] Gateway request failed: {exc}")
        return 1

    model = response.get("model")
    message = response.get("message", {})
    content = message.get("content", "") if isinstance(message, dict) else str(message)

    print(f"Model: {model}")
    print("Raw response:")
    print(content)

    candidate = extract_python_code(content)
    if not candidate:
        print("[FAIL] No Python code block found in the response.")
        return 2

    exit_code, pytest_output = run_pytest_with_candidate(candidate)
    print("\nPytest result:")
    print(pytest_output)

    if exit_code == 0:
        print("\n[PASS] The returned file passes the fixture tests.")
        return 0

    print("\n[FAIL] The returned file did not pass the fixture tests.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
