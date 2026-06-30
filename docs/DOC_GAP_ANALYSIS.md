# Documentation Gap Analysis

## Executive Summary
Identified 14 critical contradictions between documentation and code implementation.

## Critical Issues Fixed

### 1. Configuration Model: Missing FERNET_KEY
- **Issue:** docs only listed INTERNAL_SECRET as required
- **Fix:** Added FERNET_KEY as required bootstrap variable
- **Source:** services/config.py:42-43

### 2. Raven Timeout Values Mismatch
- **Before:** docs said 600s max, 240s hung threshold
- **After:** 1800s max, 600s hung threshold
- **Source:** services/config.py:69-72

### 3. Log Max Entries Mismatch
- **Before:** docs said 50000
- **After:** 10000
- **Source:** services/config.py:68

### 4. MA Streaming Architecture
- **Before:** docs described OpenSubsonic direct streaming
- **After:** WebSocket flow stream proxy (see MA_STREAMING_FIX.md)
- **Source:** services/gateway/ma_ws_client.py

### 5. YouTube Search Fallback
- **Before:** 2-tier (yt-dlp, SearXNG)
- **After:** 3-tier (yt-dlp, SearXNG, Playwright)
- **Source:** services/execution/handlers/video.py:59-210

### 6. Roku Wake Prerequisite
- **Missing:** remote.* entity requirement
- **Fix:** Added prerequisite note
- **Source:** services/execution/handlers/roku.py:256-268

### 7. Android TV Delegation
- **Incomplete:** Missing MA wrapper exclusion
- **Fix:** Added exclusion criteria
- **Source:** services/execution/handlers/android_tv.py:97-145

### 8. Power Routing Speaker Exclusion
- **Missing:** device_class=speaker exclusion logic
- **Fix:** Added clarification
- **Source:** services/gateway/main.py:1450-1464

## Undocumented Services (Now Fixed)
- [x] dns-sync service → docs/DNS_SYNC_SERVICE.md
- [x] DNS Resolver → docs/DNS_RESOLVER.md
- [x] Control Plane → docs/CONTROL_PLANE_SERVICE.md
- [x] Automation → docs/AUTOMATION_SERVICE.md

## Verification
All fixes verified against source code. No assumptions made.
