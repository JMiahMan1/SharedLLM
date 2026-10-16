"""Waiting for a workspace's sync lock no longer freezes the runtime."""
import asyncio
import threading
import time

from services.workspace_runtime import main as runtime


async def test_waiting_for_the_lock_leaves_the_event_loop_free():
    lock = runtime.get_workspace_lock("lock-test")
    taken = threading.Event()

    def long_sync():
        with lock:
            taken.set()
            time.sleep(0.4)

    threading.Thread(target=long_sync).start()
    taken.wait()
    ticks = 0

    async def other_requests():
        nonlocal ticks
        for _ in range(10):
            await asyncio.sleep(0.02)
            ticks += 1

    async def write_while_locked():
        async with runtime.held_workspace_lock("lock-test") as run_locked:
            # write_file takes the same lock again on the holder thread.
            return await run_locked(lambda: lock.acquire() and (lock.release() or "written"))

    result, _ = await asyncio.gather(write_while_locked(), other_requests())
    assert result == "written"
    assert ticks == 10
    assert lock.acquire(blocking=False)  # released afterwards
    lock.release()


async def test_the_loop_runs_while_the_lock_is_held_elsewhere():
    lock = runtime.get_workspace_lock("lock-test-2")
    taken, done = threading.Event(), threading.Event()

    def holder():
        with lock:
            taken.set()
            done.wait(2)

    threading.Thread(target=holder).start()
    taken.wait()
    cm = runtime.held_workspace_lock("lock-test-2")
    waiting = asyncio.create_task(cm.__aenter__())
    await asyncio.sleep(0.05)  # would never get here if acquire blocked the loop
    assert not waiting.done()
    done.set()
    assert callable(await asyncio.wait_for(waiting, 2))
    await cm.__aexit__(None, None, None)
    assert lock.acquire(blocking=False)
    lock.release()
