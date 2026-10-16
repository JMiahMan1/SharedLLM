import { describe, expect, it } from 'vitest';
import { chatReplyText } from './chatReply';

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
