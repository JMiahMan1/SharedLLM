/** What went wrong, in the server's own words when it gave any: FastAPI's
 *  `detail` string, else the error's message, else `fallback`. */
export function errorText(e: unknown, fallback = 'Something went wrong'): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return e instanceof Error ? e.message : fallback;
}
