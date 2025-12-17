#!/usr/bin/env python3
import asyncio
import sys
sys.path.insert(0, '/workspace')

from app.settings import GlobalResources
from app.logic.refresh_devices import refresh_db

async def refresh():
    print("Starting device refresh...")
    await refresh_db()
    print('Device refresh completed')

if __name__ == '__main__':
    asyncio.run(refresh())
