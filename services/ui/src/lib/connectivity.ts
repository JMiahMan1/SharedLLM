/** Bare host -> https (the public server is https-only); no trailing slash. */
export function normalizeServerUrl(raw: string): string {
  let url = raw.trim();
  if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
  return url.replace(/\/+$/, '');
}

export async function checkConnectivity(url: string): Promise<{ ok: boolean; latency?: number; error?: string }> {
  if (!url) return { ok: false, error: 'No server URL configured' };

  const startTime = Date.now();
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);

    // The gateway's own liveness: answers fast, and does not fan out to
    // every backing service like /health/ready (this runs every 30 s on
    // each phone).
    const response = await fetch(`${url}/health`, {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store',
    });
    clearTimeout(timeout);

    if (response.ok) {
      return { ok: true, latency: Date.now() - startTime };
    }
    return { ok: false, error: `Server returned ${response.status}` };
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      return { ok: false, error: 'Connection timed out' };
    }
    return { ok: false, error: err instanceof Error ? err.message : 'Unknown error' };
  }
}

export async function checkInternetAccess(): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    await fetch('https://www.google.com/generate_204', {
      method: 'GET',
      signal: controller.signal,
      mode: 'no-cors',
    });
    clearTimeout(timeout);
    return true;
  } catch {
    return false;
  }
}
