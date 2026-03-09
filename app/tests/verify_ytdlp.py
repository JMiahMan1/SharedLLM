
import asyncio
import logging
import sys
import os
import time

# Add app to path
sys.path.append(os.getcwd())

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("YtDlpTest")

from app.utils.video_cache import download_video_progressive, get_video_id
from app.domains.media.integrations.standard import StandardIntegration

async def run_test():
    print("\n=== Video Download Diagnostic ===")
    
    query = "Tim Timmons"
    print(f"Query: {query}")
    
    # 1. Resolve URL (Simulate VideoHelperMixin._search_video_url logic)
    # Using StandardIntegration's search logic
    std = StandardIntegration()
    print("Searching for video URL...")
    # StandardIntegration._search_video_url is internal, but logic is roughly:
    # web_search -> regex
    # We can try to use VideoHelperMixin._search_and_filter_video_url if we can import it or just use the logic from StandardIntegration
    
    # Actually, verify_roku_intents.py confirmed that 'watch Tim Timmons' worked logic-wise to get here.
    # Let's assume we find a valid URL. I'll search for one manually or use a helper.
    
    # Let's assume we want to search via the same method as the app.
    # But for a quick test, let's just search for "Tim Timmons music video" via yt-dlp search or assume we get a URL.
    
    # Actually, I'll stick to what the code uses: StandardIntegration._search_video_url
    url = await std._search_video_url(query)
    
    if not url:
        print("[FAIL] Could not find video URL via search")
        return

    print(f"Found URL: {url}")
    
    # 2. Test Download Speed
    video_id = get_video_id(url)
    print(f"Video ID: {video_id}")
    
    print("Starting progressive download...")
    start_time = time.time()
    
    path, ready = await download_video_progressive(url, video_id)
    
    end_time = time.time()
    duration = end_time - start_time
    
    if ready:
        print(f"[SUCCESS] Buffer Ready in {duration:.2f} seconds")
        print(f"File Size: {path.stat().st_size / 1024 / 1024:.2f} MB")
        
        # Check file type
        import subprocess
        try:
             res = subprocess.run(["file", str(path)], capture_output=True, text=True)
             print(f"File Type: {res.stdout.strip()}")
        except: 
             print("Could not run 'file' command")
    else:
        print(f"[FAIL] Download timed out or failed (Duration: {duration:.2f}s)")

if __name__ == "__main__":
    asyncio.run(run_test())
