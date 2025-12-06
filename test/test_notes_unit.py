import asyncio
import sys
import os
from dotenv import load_dotenv

# Setup path to allow importing app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load env before importing settings
load_dotenv(".env")

from app.logic.note_ops import create_note, read_note, append_note

async def test_notes_direct():
    print("--- Testing Note Ops Directly ---")
    
    title = f"UnitTest_{int(asyncio.get_event_loop().time())}"
    
    # 1. Create
    print(f"[1] Creating '{title}'...")
    res = await create_note(title, "Unit Test Content", "Testing")
    print(res)
    assert res["status"] == "success"

    # 2. Read
    print(f"[2] Reading '{title}'...")
    res = await read_note(title)
    print(res)
    assert res["status"] == "success"
    assert "Unit Test Content" in res["content"]

    # 3. Append
    print(f"[3] Appending...")
    res = await append_note(title, "Appended Item")
    print(res)
    assert res["status"] == "success"

    # 4. Verify Append
    res = await read_note(title)
    assert "Appended Item" in res["content"]
    
    print("✅ Unit Test Passed")

if __name__ == "__main__":
    asyncio.run(test_notes_direct())
