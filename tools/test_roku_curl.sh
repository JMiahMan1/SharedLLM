#!/bin/bash
# Simple Roku ECP test using curl
# Tests video playback with already downloaded media

ROKU_IP="192.168.2.166"
VIDEO_URL="http://192.168.2.211:11435/cast_video/1c504eb5640b.mp4"

echo "========================================"
echo "Roku ECP Video Playback Test"
echo "========================================"

# Step 0: Power on TV and wake display
echo ""
echo "0. Powering on TV..."
curl -X POST "http://${ROKU_IP}:8060/keypress/PowerOn" 2>&1 | head -1
sleep 3
echo "   Sending Home button to wake display..."
curl -X POST "http://${ROKU_IP}:8060/keypress/Home" 2>&1 | head -1
sleep 2

# Step 1: Query Roku for installed apps
echo ""
echo "1. Querying Roku for installed apps..."
curl -s "http://${ROKU_IP}:8060/query/apps" | grep -E "Roku Media Player|Play On Roku" | head -5

# Step 2: Use channel 15985 (Play On Roku) - this accepts external URLs
echo ""
echo "2. Using channel 15985 (Play On Roku) for external video..."

# Step 3: Test video URL accessibility
echo ""
echo "3. Testing if video URL is accessible..."
curl -s -o /dev/null -w "HTTP Status: %{http_code}\n" "$VIDEO_URL"

# Step 4: Send play command to Roku via /launch endpoint
echo ""
echo "4. Sending play command to Roku via /launch/15985..."
echo "   URL: http://${ROKU_IP}:8060/launch/15985"
echo "   Parameter: contentID=${VIDEO_URL}"

RESPONSE=$(curl -X POST -w "\n%{http_code}" \
  "http://${ROKU_IP}:8060/launch/15985?contentID=${VIDEO_URL}" \
  2>&1)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
echo "   HTTP Response: $HTTP_CODE"

# Step 5: Check what's running on Roku
sleep 2
echo ""
echo "5. Checking active app on Roku..."
curl -s "http://${ROKU_IP}:8060/query/active-app"

echo ""
echo "========================================"
echo "Test complete!"
echo "Did video play on TV? (yes/no)"
echo "========================================"
