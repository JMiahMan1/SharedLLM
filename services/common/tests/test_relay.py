import asyncio

from services.common.relay import until_either_ends


async def test_the_other_direction_is_stopped_when_one_ends():
    stopped = asyncio.Event()

    async def browser_left():
        await asyncio.sleep(0.01)

    async def waits_for_output_forever():
        try:
            await asyncio.sleep(3600)
        finally:
            stopped.set()

    await asyncio.wait_for(until_either_ends(browser_left(), waits_for_output_forever()), timeout=2)
    assert stopped.is_set()


async def test_an_error_in_one_direction_ends_the_relay():
    async def broken():
        raise ConnectionError("gone")

    await asyncio.wait_for(until_either_ends(broken(), asyncio.sleep(3600)), timeout=2)
