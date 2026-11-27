/**
 * A spoken turn must speak whatever the house answers, including the answers
 * that arrive in one piece.
 *
 * Every fast path in the gateway (the time, the weather, a calendar question, a
 * sentence trigger) answers without streaming:
 *
 *   {"model": "...", "message": {"role": "assistant", "content": "It is 1:06 PM."},
 *    "done": true, "status": "SUCCESS"}
 *
 * with no `reply` and no sentence envelopes. The phone transcribed the question,
 * posted it, received a 200 - and stayed silent, because the done handler only
 * read `reply`. On Android the same answer also arrives through CapacitorHttp,
 * which returns a finished body and no readable stream, and that was reported as
 * "Jarvis could not answer (200)".
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { api, NdjsonError } from './api';

/** What CapacitorHttp hands back: a finished body, no stream. */
function finished(body: string, ok = true, status = 200): Response {
  return {
    ok,
    status,
    body: null,
    text: async () => body,
    json: async () => JSON.parse(body),
  } as unknown as Response;
}

/** A real ndjson stream, one line per chunk. */
function streamed(lines: string[]): Response {
  const chunks = lines.map((line) => new TextEncoder().encode(`${line}\n`));
  let at = 0;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => (at < chunks.length ? { value: chunks[at++], done: false } : { done: true }),
      }),
    },
    text: async () => lines.join('\n'),
    json: async () => ({}),
  } as unknown as Response;
}

const fastPath = JSON.stringify({
  model: 'qwen',
  message: { role: 'assistant', content: 'It is 1:06 PM.' },
  done: true,
  status: 'SUCCESS',
});

describe('a voice turn whose answer arrives in one piece', () => {
  let heard: { text: string; voice: string | null }[] = [];
  let said: string[] = [];

  const ask = () =>
    api.voiceChatStream('What time is it?', {
      onSentence: (sentence) => heard.push(sentence),
      onReply: (text) => said.push(text),
    });

  beforeEach(() => {
    heard = [];
    said = [];
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('speaks a fast-path answer that carries no reply and no sentence envelopes', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => finished(fastPath)));

    const full = await ask();

    expect(full).toBe('It is 1:06 PM.');
    expect(heard).toEqual([{ text: 'It is 1:06 PM.', voice: null }]);
    expect(said).toEqual(['It is 1:06 PM.']);
  });

  it('keeps the sentence voice when the answer reports one', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => finished(fastPath.replace('"SUCCESS"', '"SUCCESS", "voice": "af_heart"'))),
    );

    await ask();

    expect(heard).toEqual([{ text: 'It is 1:06 PM.', voice: 'af_heart' }]);
  });

  it('reads ndjson from a finished body when the bridge gives no stream', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        finished(
          [
            JSON.stringify({ message: { content: 'Paris is the capital.' }, speech: true }),
            JSON.stringify({ done: true, reply: 'Paris is the capital.' }),
          ].join('\n'),
        ),
      ),
    );

    const full = await ask();

    expect(full).toBe('Paris is the capital.');
    expect(heard).toEqual([{ text: 'Paris is the capital.', voice: null }]);
    expect(said).toEqual(['Paris is the capital.']);
  });

  it('still speaks a streamed answer once, taking the reply for the full text', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        streamed([
          JSON.stringify({ message: { content: 'Hello there!' }, speech: true }),
          JSON.stringify({ message: { content: 'What can I do?' }, speech: true }),
          JSON.stringify({ done: true, reply: 'Hello there! What can I do?' }),
        ]),
      ),
    );

    const full = await ask();

    expect(full).toBe('Hello there! What can I do?');
    expect(heard.map((sentence) => sentence.text)).toEqual(['Hello there!', 'What can I do?']);
    expect(said).toEqual(['Hello there! What can I do?']);
  });

  it('speaks nothing twice when a stream ends with the whole answer in one envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        streamed([
          JSON.stringify({ message: { content: 'Done.' }, speech: true }),
          JSON.stringify({ done: true, speech_rest: 'And dusted.' }),
        ]),
      ),
    );

    await ask();

    expect(heard.map((sentence) => sentence.text)).toEqual(['Done.', 'And dusted.']);
  });

  it('reports a refusal with the server’s own words', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => finished('{"detail":"that model is offline"}', false, 503)));

    await expect(ask()).rejects.toBeInstanceOf(NdjsonError);
    await expect(ask()).rejects.toThrow('that model is offline');
  });
});
