import os
import time

from services.common.proc import run_process


async def test_a_timeout_kills_the_children_too(tmp_path):
    pidfile = tmp_path / "child.pid"
    # The shell starts a child that would outlive a kill of the shell alone.
    rc, _, _ = await run_process(f"sleep 30 & echo $! > {pidfile}; wait", cwd=None, env=None, shell=True, timeout=0.5)
    assert rc is None
    child = int(pidfile.read_text())
    time.sleep(0.2)
    try:
        os.kill(child, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive


async def test_a_normal_command_returns_its_output():
    rc, out, err = await run_process(["sh", "-c", "echo hi; echo oops >&2; exit 3"], cwd=None, env=None, shell=False, timeout=5)
    assert (rc, out, err) == (3, "hi\n", "oops\n")
