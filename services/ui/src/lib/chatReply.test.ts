import { describe, expect, it } from 'vitest';
import { chatFailureText, chatReplyText, isOfflineFailure } from './chatReply';

describe('chatReplyText', () => {
  it('reads the Ollama-shaped answer the gateway sends by default', () => {
    expect(chatReplyText({ status: 'SUCCESS', message: { role: 'assistant', content: 'Hi there' } })).toBe('Hi there');
  });

  it('reads OpenAI-shaped answers', () => {
    expect(chatReplyText({ choices: [{ message: { content: 'Yes' } }] })).toBe('Yes');
  });

  it('reads a plain message string (errors, queued Raven missions)', () => {
    expect(chatReplyText({ status: 'queued', message: 'Raven mission #4 dispatched.' })).toBe('Raven mission #4 dispatched.');
  });

  it('returns null rather than an object or an empty string', () => {
    expect(chatReplyText({ message: { role: 'assistant', content: '' } })).toBeNull();
    expect(chatReplyText(null)).toBeNull();
    expect(chatReplyText('nope')).toBeNull();
  });
});

describe('chatFailureText', () => {
  const offline = "Jarvis's thinking brain (the local AI) is offline right now.";

  it('shows the sentence a failed chat turn carries, not a generic failure', () => {
    const err = { response: { status: 503, data: {
      status: 'ERROR', message: { role: 'assistant', content: offline }, error: offline, offline: 'local_ai',
    } } };
    expect(chatFailureText(err)).toBe(offline);
    expect(isOfflineFailure(err)).toBe(true);
  });

  it('reads the OpenAI error shape', () => {
    expect(chatFailureText({ response: { data: { error: { message: offline } } } })).toBe(offline);
  });

  it('has nothing to show for a lost connection', () => {
    expect(chatFailureText({ code: 'ERR_NETWORK' })).toBeNull();
    expect(chatFailureText(new Error('boom'))).toBeNull();
    expect(isOfflineFailure({ response: { data: { status: 'ERROR' } } })).toBe(false);
  });
});
