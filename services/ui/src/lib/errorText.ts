/** What went wrong, in the server's own words when it gave any: FastAPI's
 *  `detail` string, or the `message` the gateway's proxies answer with, else
 *  the error's message, else `fallback`. */
export function errorText(e: unknown, fallback = 'Something went wrong'): string {
  const data = (e as { response?: { data?: { detail?: unknown; message?: unknown } } })?.response?.data;
  if (typeof data?.detail === 'string') return data.detail;
  if (typeof data?.message === 'string') return data.message;
  return e instanceof Error ? e.message : fallback;
}
