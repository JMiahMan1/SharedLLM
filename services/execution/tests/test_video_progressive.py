"""Progressive video downloads start playback from yt-dlp's .part file and
give up on a stalled download instead of hanging the cast."""
import asyncio
import json
import pathlib

from services.execution.handlers import video


class FakeProc:
    def __init__(self, on_start=None, info=b""):
        self.returncode = None
        self._info = info
        self.killed = False
        if on_start:
            on_start()

    async def communicate(self):
        self.returncode = 0
        return self._info, b""

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


def _patch(monkeypatch, tmp_path, writes_part):
    procs = []
    monkeypatch.setattr(video, "TEMP_MEDIA_DIR", str(tmp_path))

    async def exec_(*cmd, **kwargs):
        if "--dump-json" in cmd or "-J" in cmd or "--print" in cmd:
            p = FakeProc(info=json.dumps({"title": "A video"}).encode())
        else:
            out = cmd[cmd.index("-o") + 1]
            p = FakeProc(on_start=(lambda: pathlib.Path(out + ".part").write_bytes(b"x" * 2048)) if writes_part else None)
        procs.append(p)
        return p

    monkeypatch.setattr(video.asyncio, "create_subprocess_exec", exec_)
    return procs


async def test_playback_starts_from_the_part_file(monkeypatch, tmp_path):
    procs = _patch(monkeypatch, tmp_path, writes_part=True)
    media_id, title = await asyncio.wait_for(video.download_video_progressive("https://v.example/1", threshold=1024), 5)
    assert media_id and title == "A video"
    assert not procs[-1].killed


async def test_a_stalled_download_is_stopped(monkeypatch, tmp_path):
    procs = _patch(monkeypatch, tmp_path, writes_part=False)
    monkeypatch.setattr(video, "VIDEO_PROGRESSIVE_TIMEOUT_S", 1)
    result = await asyncio.wait_for(video.download_video_progressive("https://v.example/2", threshold=1024), 5)
    assert result == (None, None)
    assert procs[-1].killed
