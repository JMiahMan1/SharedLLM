/**
 * The reply text from a non-streaming `/api/chat` answer, or null if it has
 * none.
 *
 * The gateway answers in Ollama shape (`message: {role, content}`), in OpenAI
 * shape (`choices[0].message.content`), as `{status: 'ERROR', message}`, or,
 * for a dispatched Raven mission, with a plain `message` string. Rendering
 * `message` as-is put an object into JSX and crashed the Quick Assistant
 * widget on the first ordinary reply.
 */
export function chatReplyText(resp: unknown): string | null {
  if (!resp || typeof resp !== 'object') return null;
  const r = resp as {
    response?: unknown;
    message?: unknown;
    choices?: Array<{ message?: { content?: unknown } }>;
  };
  const message =
    r.message && typeof r.message === 'object' ? (r.message as { content?: unknown }).content : r.message;
  const text = [r.response, message, r.choices?.[0]?.message?.content].find(
    (c): c is string => typeof c === 'string' && c.trim() !== '',
  );
  return text ?? null;
}

/**
 * What to show in the conversation when a chat request failed with an answer
 * from the server: its plain sentence (for example that Jarvis's thinking brain
 * is offline), or null when the failure has none (a lost connection).
 *
 * The gateway answers a failed turn with an error status *and* the sentence in
 * the usual reply fields, so it is shown as Jarvis's reply rather than as a
 * generic "Failed to send" toast that took the question away.
 */
export function chatFailureText(error: unknown): string | null {
  const data = (error as { response?: { data?: unknown } } | null)?.response?.data;
  if (!data || typeof data !== 'object') return null;
  const body = data as { status?: unknown; offline?: unknown; error?: unknown };
  const text = chatReplyText(data);
  if (text && (body.status === 'ERROR' || body.offline)) return text;
  const openai = body.error as { message?: unknown } | undefined;
  if (openai && typeof openai.message === 'string' && openai.message.trim()) return openai.message.trim();
  return null;
}

/** Whether a failed chat request was the local AI being offline (shown softer than an error). */
export function isOfflineFailure(error: unknown): boolean {
  const data = (error as { response?: { data?: { offline?: unknown } } } | null)?.response?.data;
  return Boolean(data && typeof data === 'object' && data.offline);
}
