#!/usr/bin/env python3
import asyncio
import sys
import os

# Add the app directory to Python path
sys.path.insert(0, '/workspace')

from app.settings import GlobalResources
from app.logic.media_ops import get_device_capabilities

async def test():
    # Force HA API call by temporarily disabling ChromaDB
    creds = {
        'ha_token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJhNzg4NzhjNzdlMzM0N2Q0OWQ5NWEyNjE2NmI4ODhmNiIsImlhdCI6MTc2MTk0MDE4MywiZXhwIjoyMDc3MzAwMTgzfQ.y0cxCphUIntpJznahl_k0p-ewIP5n55A7kXq1I5accQ',
        'user': 'admin'
    }
    caps = await get_device_capabilities('light.piano_lamp', creds, GlobalResources.redis_client)
    print('Capabilities:', caps)

if __name__ == '__main__':
    asyncio.run(test())
