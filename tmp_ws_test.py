import asyncio, websockets, json, os, uuid, httpx, traceback, sys

async def main():
    secret = os.environ.get('INTERNAL_SECRET', '')
    try:
        async with httpx.AsyncClient() as c:
            resp = await c.post('http://identity:8001/api/resolve', json={'user_id': 1}, headers={'X-Internal-Secret': secret}, timeout=10.0)
            creds = resp.json()
            mass_url = creds['mass_url']
            mass_token = creds['mass_token']
            
            http_base = mass_url.replace('http://', '').replace('https://', '')
            ws_url = f'ws://{http_base}/ws?token={mass_token}'
            
            async with websockets.connect(ws_url) as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                data = json.loads(msg)
                print(f'server_info: {json.dumps(data, indent=2)[:300]}', file=sys.stderr)
                
                mid = uuid.uuid4().hex
                await ws.send(json.dumps({'message_id': mid, 'command': 'auth', 'args': {'token': mass_token}}))
                msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                auth_data = json.loads(msg)
                authenticated = auth_data.get('result', {}).get('authenticated', False)
                print(f'auth: authenticated={authenticated}', file=sys.stderr)
                
                # Play a track
                mid3 = uuid.uuid4().hex
                await ws.send(json.dumps({'message_id': mid3, 'command': 'player_queues/play_media', 'args': {'queue_id': 'up309587157251', 'media': 'library://track/1885'}}))
                
                for i in range(60):
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        data = json.loads(msg)
                        print(f'\n=== Event {i}: ===', file=sys.stderr)
                        print(json.dumps(data, indent=2), file=sys.stderr)
                    except asyncio.TimeoutError:
                        print(f'\nTimeout at event {i}', file=sys.stderr)
                        break
    except Exception as e:
        traceback.print_exc()

asyncio.run(main())
