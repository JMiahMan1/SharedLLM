"""The Docker event stream ending must not leave the DNS registry frozen."""
import asyncio

from services.dns.tests.test_upstream_timeout import _load_dns


def test_a_finished_event_stream_is_rescanned_and_reopened():
    dns = _load_dns({})
    streams = []
    scans = []

    class _Client:
        def events(self, decode=True):
            streams.append(1)
            if len(streams) == 1:
                return iter([])  # the stream ends at once (daemon restarted)
            return iter([{"Type": "container", "Action": "noop"}])

    watcher = dns.DockerWatcher(_Client(), registry=None)
    watcher.RECONNECT_DELAY_CAP_S = 0

    async def fake_sync():
        scans.append(1)

    handled = []

    async def fake_handle(event):
        handled.append(event)
        watcher.stop()

    watcher._sync_containers = fake_sync
    watcher._handle_event = fake_handle

    async def run():
        watcher._running = True
        await asyncio.wait_for(watcher._watch_events(), timeout=10)

    asyncio.run(run())
    assert len(streams) == 2, "the ended stream was not reopened"
    assert scans, "missed container changes were not rescanned"
    assert handled, "events from the reopened stream were not handled"
