// Binary downloads in the Android app.
//
// With CapacitorHttp enabled, the WebView's XMLHttpRequest and fetch are sent
// by the native layer, and it hands every response it does not recognise as
// JSON back as TEXT: a picture, a book, a zip or ciphertext arrived with every
// invalid UTF-8 sequence replaced, so the file was corrupt (the private chat's
// pictures would not decrypt). The native plugin returns the exact bytes only
// when asked for `responseType: 'arraybuffer'` (as base64). This adapter asks
// for that, for every request that wants `arraybuffer` or `blob`, and decodes
// the base64 into the bytes axios would have given a browser. Nowhere but the
// native app is it used.
import { Capacitor, CapacitorHttp } from '@capacitor/core';
import { AxiosError, AxiosHeaders, type AxiosAdapter, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';

export function wantsNativeBinary(config: Pick<InternalAxiosRequestConfig, 'responseType'>, native = Capacitor.isNativePlatform()): boolean {
  return native && (config.responseType === 'arraybuffer' || config.responseType === 'blob');
}

export function base64ToBytes(base64: string): Uint8Array<ArrayBuffer> {
  const binary = atob(base64.replace(/\s+/g, ''));
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
  return out;
}

function plainHeaders(headers: InternalAxiosRequestConfig['headers']): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(AxiosHeaders.from(headers).toJSON())) {
    if (value != null) out[key] = Array.isArray(value) ? value.join(', ') : String(value);
  }
  return out;
}

/** The request body as the native plugin takes it: JSON as an object, anything else as given. */
function nativeBody(data: unknown): unknown {
  if (typeof data !== 'string') return data ?? undefined;
  try {
    return JSON.parse(data);
  } catch {
    return data;
  }
}

function headerValue(headers: Record<string, string>, name: string): string | undefined {
  const key = Object.keys(headers).find((k) => k.toLowerCase() === name);
  return key ? headers[key] : undefined;
}

export const nativeBinaryAdapter: AxiosAdapter = async (config) => {
  const url = new URL(config.url ?? '', config.baseURL || window.location.href);
  for (const [key, value] of Object.entries(config.params ?? {})) {
    if (value != null) url.searchParams.set(key, String(value));
  }
  const method = (config.method ?? 'get').toUpperCase();
  const native = await CapacitorHttp.request({
    url: url.toString(),
    method,
    headers: plainHeaders(config.headers),
    data: method === 'GET' || method === 'HEAD' ? undefined : nativeBody(config.data),
    responseType: 'arraybuffer',
    connectTimeout: config.timeout || undefined,
    readTimeout: config.timeout || undefined,
  });
  const headers = native.headers ?? {};
  const contentType = headerValue(headers, 'content-type') ?? 'application/octet-stream';
  let data: unknown = native.data;
  if (typeof native.data === 'string') {
    const bytes = base64ToBytes(native.data);
    data = config.responseType === 'blob' ? new Blob([bytes], { type: contentType }) : bytes.buffer;
  }
  const response: AxiosResponse = {
    data,
    status: native.status,
    statusText: '',
    headers: AxiosHeaders.from(headers),
    config,
    request: null,
  };
  const valid = config.validateStatus ?? ((status: number) => status >= 200 && status < 300);
  if (!valid(native.status)) {
    throw new AxiosError(
      `Request failed with status code ${native.status}`,
      native.status >= 500 ? AxiosError.ERR_BAD_RESPONSE : AxiosError.ERR_BAD_REQUEST,
      config,
      null,
      response,
    );
  }
  return response;
};
