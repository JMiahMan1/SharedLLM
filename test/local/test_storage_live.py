import pytest
import os

from services.storage.providers import build_provider
from services.storage.models import ProviderConfig

# Live credentials from environment
NC_URL = os.getenv("NC_URL")
NC_USER = os.getenv("NC_USER")
NC_PASS = os.getenv("NC_PASS")

@pytest.mark.skipif(not NC_URL, reason="NC_URL not set - skipping live integration test")
@pytest.mark.asyncio
async def test_live_nextcloud_connection():
    """Verify we can actually talk to the user's NextCloud."""
    config = ProviderConfig(
        kind="nextcloud",
        settings={
            "url": NC_URL,
            "username": NC_USER,
            "password": NC_PASS
        }
    )
    provider = build_provider(config)
    entries = provider.list_entries(path="/", recursive=False)
    assert isinstance(entries, list)
    print(f"\nSuccessfully listed {len(entries)} entries from live NextCloud.")

@pytest.mark.skipif(not NC_URL, reason="NC_URL not set - skipping live integration test")
@pytest.mark.asyncio
async def test_live_content_extraction():
    """Verify we can extract content from a real file."""
    config = ProviderConfig(
        kind="nextcloud",
        settings={
            "url": NC_URL,
            "username": NC_USER,
            "password": NC_PASS
        }
    )
    provider = build_provider(config)
    entries = provider.list_entries(path="/", recursive=True)
    
    txt_files = [e for e in entries if not e.is_dir and (e.path.endswith(".txt") or e.path.endswith(".md"))]
    
    if not txt_files:
        pytest.skip("No text/md files found in NextCloud to test extraction.")
        
    target = txt_files[0]
    content = provider.get_content(target.path)
    assert content is not None
    assert len(content) > 0
    print(f"\nSuccessfully extracted {len(content)} chars from {target.path}")
