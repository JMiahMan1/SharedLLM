import { describe, expect, it, vi, beforeEach } from 'vitest';
import type { InternalAxiosRequestConfig } from 'axios';
import { AxiosHeaders } from 'axios';

const request = vi.fn();
vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: () => false },
  CapacitorHttp: { request: (...args: unknown[]) => request(...args) },
}));

import { base64ToBytes, nativeBinaryAdapter, wantsNativeBinary } from './nativeBinary';

const config = (over: Partial<InternalAxiosRequestConfig> = {}): InternalAxiosRequestConfig => ({
  url: '/api/private-chat/chats/abc/blobs/1',
  baseURL: 'https://jarvis.example.test',
  method: 'get',
  responseType: 'arraybuffer',
  timeout: 180_000,
  headers: AxiosHeaders.from({ Authorization: 'Bearer test-key', Accept: 'application/json' }),
  ...over,
});

// Bytes that are not valid UTF-8, which a text response would have mangled.
const BYTES = Uint8Array.from([0x00, 0xff, 0xfe, 0x80, 0xc3, 0x28, 0x7f]);
const B64 = btoa(String.fromCharCode(...BYTES));

describe('binary downloads in the Android app', () => {
  beforeEach(() => request.mockReset());

  it('is only used in the native app, and only for binary responses', () => {
    expect(wantsNativeBinary({ responseType: 'arraybuffer' }, true)).toBe(true);
    expect(wantsNativeBinary({ responseType: 'blob' }, true)).toBe(true);
    expect(wantsNativeBinary({ responseType: 'json' }, true)).toBe(false);
    expect(wantsNativeBinary({ responseType: 'arraybuffer' }, false)).toBe(false);
  });

  it('decodes base64 to the exact bytes', () => {
    expect(Array.from(base64ToBytes(B64))).toEqual(Array.from(BYTES));
  });

  it('asks the native plugin for bytes and returns them byte for byte, signed in', async () => {
    request.mockResolvedValue({ status: 200, data: B64, headers: { 'Content-Type': 'application/octet-stream' } });
    const res = await nativeBinaryAdapter(config());
    expect(new Uint8Array(res.data as ArrayBuffer)).toEqual(BYTES);
    const sent = request.mock.calls[0][0];
    expect(sent).toMatchObject({
      url: 'https://jarvis.example.test/api/private-chat/chats/abc/blobs/1',
      method: 'GET',
      responseType: 'arraybuffer',
      readTimeout: 180_000,
    });
    expect(sent.headers.Authorization).toBe('Bearer test-key');
    expect(sent.data).toBeUndefined();
  });

  it('sends a POST body as JSON and can answer with a Blob of the right type', async () => {
    request.mockResolvedValue({ status: 200, data: B64, headers: { 'content-type': 'application/zip' } });
    const res = await nativeBinaryAdapter(config({
      method: 'post', url: '/api/workspaces/zip', responseType: 'blob', data: JSON.stringify({ paths: ['a.txt'] }),
      params: { workspace: 'w1' },
    }));
    const sent = request.mock.calls[0][0];
    expect(sent.data).toEqual({ paths: ['a.txt'] });
    expect(sent.url).toBe('https://jarvis.example.test/api/workspaces/zip?workspace=w1');
    const blob = res.data as Blob;
    expect(blob.type).toBe('application/zip');
    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(BYTES);
  });

  it('rejects an error status the way axios does, with the response attached', async () => {
    request.mockResolvedValue({ status: 404, data: { detail: 'No such picture.' }, headers: { 'content-type': 'application/json' } });
    await expect(nativeBinaryAdapter(config())).rejects.toMatchObject({
      response: { status: 404, data: { detail: 'No such picture.' } },
    });
  });
});
