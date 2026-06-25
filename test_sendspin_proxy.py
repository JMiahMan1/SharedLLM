"""Quick manual check for browser -> sendspin_proxy -> MA sendspin flow.

This is intentionally not a pytest test module. Run it manually with:

    SENDSPIN_PROXY_WS=ws://... MA_DIRECT_WS=ws://... MA_TOKEN=... python3 test_sendspin_proxy.py
"""

from __future__ import annotations

import asyncio
import json
import os


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


async def run_sendspin_proxy(proxy_ws: str) -> None:
    import websockets

    async with websockets.connect(proxy_ws) as ws:
        print("Connected to gateway sendspin proxy")

        hello = {
            "type": "client/hello",
            "payload": {
                "client_id": "test-browser-client",
                "name": "TestBrowser",
                "version": 1,
                "supported_roles": ["player@v1", "controller@v1", "metadata@v1"],
                "device_info": {
                    "product_name": "Web Browser",
                    "manufacturer": "Mozilla",
                    "software_version": "Test",
                },
                "player@v1_support": {
                    "supported_formats": [
                        {"codec": "opus", "channels": 2, "sample_rate": 48000, "bit_depth": 16},
                        {"codec": "flac", "channels": 2, "sample_rate": 48000, "bit_depth": 24},
                    ],
                    "buffer_capacity": 5242880,
                    "supported_commands": ["volume", "mute"],
                },
            },
        }
        print(f"Sending: {json.dumps(hello)[:200]}")
        await ws.send(json.dumps(hello))

        print("Waiting for response...")
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=10.0)
            print(f"Response: {response[:500]}")
        except asyncio.TimeoutError:
            print("Timeout - no response from MA")
            return

        player_cmd = {
            "type": "client/command",
            "message_id": "test-player-cmd-1",
            "payload": {},
        }
        await ws.send(json.dumps(player_cmd))
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=5.0)
            print(f"Players: {response[:500]}")
        except asyncio.TimeoutError:
            print("No player response")


async def run_ma_direct(ma_ws: str, ma_token: str) -> None:
    import websockets

    async with websockets.connect(ma_ws) as ws:
        print("\n=== Direct MA test ===")
        auth = {"type": "auth", "token": ma_token}
        print(f"Sending auth: token={ma_token[:8]}...")
        await ws.send(json.dumps(auth))

        response = await asyncio.wait_for(ws.recv(), timeout=10.0)
        print(f"Auth response: {response[:500]}")

        hello = {
            "type": "client/hello",
            "message_id": "direct-test-1",
            "payload": {
                "client_id": "direct-test-client",
                "name": "DirectTest",
                "version": 1,
                "supported_roles": ["player@v1", "controller@v1", "metadata@v1"],
                "device_info": {
                    "product_name": "Web Browser",
                    "manufacturer": "Mozilla",
                    "software_version": "Test",
                },
                "player@v1_support": {
                    "supported_formats": [
                        {"codec": "opus", "channels": 2, "sample_rate": 48000, "bit_depth": 16},
                    ],
                    "buffer_capacity": 5242880,
                    "supported_commands": ["volume", "mute"],
                },
            },
        }
        print(f"Sending client/hello: {json.dumps(hello)[:200]}")
        await ws.send(json.dumps(hello))

        try:
            response = await asyncio.wait_for(ws.recv(), timeout=5.0)
            print(f"Hello response: {response[:500]}")
        except asyncio.TimeoutError:
            print("Hello: no response (timeout)")
        except websockets.ConnectionClosed as exc:
            print(f"Hello: connection closed code={exc.code} reason={exc.reason}")


async def main() -> None:
    proxy_ws = _require_env("SENDSPIN_PROXY_WS")
    ma_ws = _require_env("MA_DIRECT_WS")
    ma_token = _require_env("MA_TOKEN")

    print("=== Gateway Proxy Test ===")
    await run_sendspin_proxy(proxy_ws)
    print("\n=== Direct MA Test ===")
    await run_ma_direct(ma_ws, ma_token)


if __name__ == "__main__":
    asyncio.run(main())
