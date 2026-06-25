#!/usr/bin/env python3
"""Test sendspin WebSocket connection - client/hello first (per gateway protocol)."""
import asyncio
import websockets
import json
import sys

API_TOKEN = "fd5547e6969875f231e35d5ca5a64fcc8746350bb911bcbb"

async def test_sendspin():
    url = f"ws://192.168.2.205:8080/api/sendspin?token={API_TOKEN}"
    print(f"[1] Connecting to {url}...")
    
    async with websockets.connect(url) as ws:
        print("[2] WS connected - 101 OK")
        
        # Send client/hello FIRST (per gateway protocol)
        client_hello = {
            "type": "client/hello",
            "payload": {"client_id": "test-client-001"},
            "clientName": "TestBrowser",
            "message_id": "test-msg-001",
            "supported_formats": [
                {"codec": "opus", "channels": 2, "sample_rate": 48000, "bit_depth": 16}
            ]
        }
        print(f"[3] Sending client/hello: {json.dumps(client_hello)}")
        await ws.send(json.dumps(client_hello))
        
        try:
            resp = await asyncio.wait_for(ws.recv(), timeout=10)
            print(f"[4] Response: {resp[:500]}")
        except asyncio.TimeoutError:
            print("[4] Timeout waiting for server/hello")
        
        # Listen for a few more messages
        print("[5] Listening for 5 seconds...")
        for i in range(10):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                print(f"  <-- {msg[:300]}")
            except asyncio.TimeoutError:
                pass
        
        # Try to play a track
        print("\n[6] Trying to play a test track...")
        play_msg = {
            "type": "client/play",
            "message_id": "test-play-001",
            "payload": {
                "track_id": "spotify:track:4cOdK2wGLETKBW3PvgPWqT",
                "volume": 50
            }
        }
        await ws.send(json.dumps(play_msg))
        
        try:
            resp = await asyncio.wait_for(ws.recv(), timeout=5)
            print(f"  play response: {resp[:300]}")
        except asyncio.TimeoutError:
            print("  No response (may need valid track)")
        
        # Send goodbye
        goodbye = {"type": "client/goodbye", "payload": {"reason": "test complete"}}
        await ws.send(json.dumps(goodbye))
        print("[7] Goodbye sent, closing...")

async def test_external_https():
    url = f"wss://jarvis.sumemail.com/api/sendspin?token={API_TOKEN}"
    print(f"\n[EXT-1] Connecting to HTTPS: {url}")
    
    async with websockets.connect(url) as ws:
        print("[EXT-2] HTTPS WS connected!")
        
        client_hello = {
            "type": "client/hello",
            "payload": {"client_id": "test-ext-001"},
            "clientName": "ExtTestBrowser",
            "message_id": "ext-test-001",
            "supported_formats": [
                {"codec": "opus", "channels": 2, "sample_rate": 48000, "bit_depth": 16}
            ]
        }
        print(f"[EXT-3] Sending client/hello")
        await ws.send(json.dumps(client_hello))
        
        resp = await asyncio.wait_for(ws.recv(), timeout=10)
        print(f"[EXT-4] Response: {resp[:500]}")
        
        goodbye = {"type": "client/goodbye", "payload": {"reason": "test"}}
        await ws.send(json.dumps(goodbye))
        print("\n[EXT-SUCCESS] HTTPS working!")

if __name__ == "__main__":
    print("=" * 60)
    print("Testing internal sendspin...")
    print("=" * 60)
    asyncio.run(test_sendspin())
    
    print("\n" + "=" * 60)
    print("Testing external HTTPS sendspin...")
    print("=" * 60)
    asyncio.run(test_external_https())
