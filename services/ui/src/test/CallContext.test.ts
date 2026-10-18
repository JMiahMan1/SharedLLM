import { describe, expect, it } from 'vitest';
import { callSocketUrl } from '../context/CallContext';

describe('call signalling URL', () => {
  it('uses wss on an https server and encodes the room details', () => {
    expect(callSocketUrl('https://jarvis.example', 'api token', 'room-1', 'Family call & co')).toBe(
      'wss://jarvis.example/api/communication/calls/ws?token=api+token&room=room-1&name=Family+call+%26+co',
    );
  });

  it('uses ws on a local http server', () => {
    expect(callSocketUrl('http://192.168.2.205:11435', 'token', 'family', 'Family')).toBe(
      'ws://192.168.2.205:11435/api/communication/calls/ws?token=token&room=family&name=Family',
    );
  });
});
