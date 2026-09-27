"""Gateway test suite conftest.

Media fixtures live in conftest_media.py (named per the media overhaul plan);
re-export them here so pytest auto-discovers them.
"""
from conftest_media import client, fake_identity, upstream  # noqa: F401
