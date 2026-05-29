export async function checkConnectivity(url: string): Promise<{ ok: boolean; latency?: number; error?: string }> {
  if (!url) return { ok: false, error: 'No server URL configured' };

  const startTime = Date.now();
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);

    const response = await fetch(`${url}/health/ready`, {
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
