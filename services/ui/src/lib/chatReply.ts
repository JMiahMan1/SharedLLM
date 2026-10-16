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
