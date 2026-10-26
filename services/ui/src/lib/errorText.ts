/** What went wrong, in the server's own words when it gave any: FastAPI's
 *  `detail` string, the first `msg` of its validation-error array, or the
 *  `message` the gateway's proxies answer with, else the error's message, else
 *  `fallback`. */
export function errorText(e: unknown, fallback = 'Something went wrong'): string {
  const data = (e as { response?: { data?: { detail?: unknown; message?: unknown } } })?.response?.data;
  if (typeof data?.detail === 'string' && data.detail.trim()) return data.detail;
  // A validation error comes back as an array of {loc, msg, type}. Its first
  // msg is the one worth showing ("String should have at least 1 character");
  // without this the caller saw "Request failed with status code 422".
  const detail = data?.detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg;
  if (typeof data?.message === 'string') return data.message;
  return e instanceof Error ? e.message : fallback;
}
