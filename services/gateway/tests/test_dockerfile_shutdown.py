"""The gateway stops quickly enough that a deploy does not stall every phone.

The push stream and the chat long polls never end on their own, so without a
graceful-shutdown limit uvicorn waited for them until Docker killed it, and the
new gateway could only start after that: the app saw 15-25 s of hung requests
on every deploy.
"""

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def _cmd() -> str:
    lines = [line for line in DOCKERFILE.read_text().splitlines() if line.startswith("CMD ")]
    assert len(lines) == 1
    return lines[0]


def test_the_gateway_bounds_its_graceful_shutdown():
    cmd = _cmd()
    assert '"--timeout-graceful-shutdown"' in cmd
    seconds = int(cmd.split('"--timeout-graceful-shutdown", "')[1].split('"')[0])
    # Under Docker's 10 s stop timeout, with room for ordinary requests to finish.
    assert 1 <= seconds < 10
