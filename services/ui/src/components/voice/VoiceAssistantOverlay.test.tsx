import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

/**
 * The sheet is the only thing the person sees, so the rules about *what* it shows
 * are as important as the turn itself: a new question starts from a clean sheet
 * (the last answer must not run into this one's), writing closes the microphone,
 * and reopening the sheet does not stay in the writing mode of the last visit.
 */

const { stop, start, finish, ask } = vi.hoisted(() => ({
  stop: vi.fn(async () => undefined),
  start: vi.fn(async () => undefined),
  finish: vi.fn(async () => undefined),
  ask: vi.fn(async () => undefined),
}));
let listener: ((event: { type: string; text?: string; level?: number }) => void) | null = null;

vi.mock('../../plugins/jarvisAssist', () => ({
  JarvisAssist: {
    addListener: async (_name: string, fn: (event: { type: string; text?: string }) => void) => {
      listener = fn;
      return {
        remove: async () => {
          listener = null;
        },
      };
    },
    start,
    stop,
    finish,
    stopSpeaking: async () => undefined,
    status: async () => ({ running: false, state: 'idle' }),
    speak: async () => undefined,
    setWakePhrase: async () => undefined,
    assistantRole: async () => ({ canHold: false, held: false }),
    requestAssistantRole: async () => undefined,
  },
  askJarvis: ask,
  type: {},
}));

import VoiceAssistantOverlay from './VoiceAssistantOverlay';

/*
 * The five things that made the sheet feel broken, each with the behaviour that
 * replaced them: an answer is not re-armed away, the centre button means what it
 * says in every state, the listener is attached before the microphone opens, a
 * new turn starts clean, and the transcript has room to scroll.
 */
describe('the sheet after Jarvis answers', () => {
  it('leaves the answer on screen instead of listening again on its own', async () => {
    vi.useFakeTimers();
    try {
      render(<VoiceAssistantOverlay isOpen onClose={() => undefined} />);
      await act(async () => undefined);
      start.mockClear();

      await emit({ type: 'transcript', text: 'how tall is everest' });
      await emit({ type: 'reply', text: '8,849 metres.' });
      await emit({ type: 'state', text: 'idle' });
      await act(async () => { vi.advanceTimersByTime(3000); });

      // The person reads the answer; nothing re-opens the microphone behind them.
      expect(start).not.toHaveBeenCalled();
      expect(screen.getByText('8,849 metres.')).toBeTruthy();
      expect(screen.queryByText('I did not hear anything.')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('the centre button says what it will do in each state', async () => {
    render(<VoiceAssistantOverlay isOpen onClose={() => undefined} />);
    await act(async () => undefined);
    // Listening: finishing submits, it never closes the sheet.
    const done = screen.getByTestId('voice-primary');
    // The icon carries the meaning; the label is what a screen reader says.
    expect(done.getAttribute('aria-label')).toContain('Done speaking');
    expect(done.getAttribute('data-state')).toBe('listening');
    fireEvent.click(done);
    expect(finish).toHaveBeenCalled();

    await emit({ type: 'state', text: 'thinking' });
    expect(screen.getByTestId('voice-primary').getAttribute('aria-label')).toContain('Cancel');

    await emit({ type: 'state', text: 'speaking' });
    expect(screen.getByTestId('voice-primary').getAttribute('aria-label')).toContain('Stop talking');

    await emit({ type: 'state', text: 'idle' });
    expect(screen.getByTestId('voice-primary').getAttribute('aria-label')).toContain('Tap to speak');
  });

  it('attaches the listener before it opens the microphone', async () => {
    render(<VoiceAssistantOverlay isOpen onClose={() => undefined} />);
    await act(async () => undefined);
    // start was called only once the listener existed, so the first words are heard.
    expect(listener).not.toBeNull();
    expect(start).toHaveBeenCalled();
  });

  it('starts the next turn clean, and asks as the person signed in', async () => {
    render(<VoiceAssistantOverlay isOpen userId="alex" onClose={() => undefined} />);
    await act(async () => undefined);
    await emit({ type: 'transcript', text: 'what time is it' });
    expect(screen.getByText('what time is it')).toBeTruthy();

    // A new turn: the old question does not sit under the new answer.
    await emit({ type: 'state', text: 'listening' });
    expect(screen.queryByText('what time is it')).toBeNull();
  });

  it('has a scrolling transcript so nothing overlaps on a small screen', async () => {
    render(<VoiceAssistantOverlay isOpen onClose={() => undefined} />);
    await act(async () => undefined);
    const transcript = screen.getByTestId('voice-transcript');
    expect(transcript.className).toContain('overflow-y-auto');
    expect(transcript.className).toContain('min-h-0');
  });
});

async function emit(event: { type: string; text?: string; level?: number }) {
  await act(async () => {
    listener?.(event);
  });
}

describe('the Ask Jarvis sheet', () => {
  it('does not run one turn’s answer into the next turn’s', async () => {
    render(<VoiceAssistantOverlay isOpen onClose={() => undefined} />);
    await act(async () => undefined);

    await emit({ type: 'transcript', text: 'what is the capital of france' });
    await emit({ type: 'sentence', text: 'Paris is the capital.' });
    await emit({ type: 'reply', text: 'Paris is the capital.' });
    expect(screen.getByText('Paris is the capital.')).toBeTruthy();

    // A new question: the previous answer goes away instead of being prefixed
    // onto the new one.
    await emit({ type: 'transcript', text: 'and of Italy' });
    await emit({ type: 'sentence', text: 'Rome.' });

    expect(screen.queryByText(/Paris/)).toBeNull();
    expect(screen.getByText('Rome.')).toBeTruthy();
  });

  it('clears the previous error when the next take starts hearing words', async () => {
    render(<VoiceAssistantOverlay isOpen onClose={() => undefined} />);
    await act(async () => undefined);

    await emit({ type: 'error', text: 'I did not hear anything.' });
    expect(screen.getByText('I did not hear anything.')).toBeTruthy();

    await emit({ type: 'partial', text: 'and of Italy' });

    expect(screen.queryByText('I did not hear anything.')).toBeNull();
  });

  it('closes the microphone when the person starts writing', async () => {
    render(<VoiceAssistantOverlay isOpen onClose={() => undefined} />);
    await act(async () => undefined);

    fireEvent.click(screen.getByLabelText('Type instead'));

    expect(stop).toHaveBeenCalled();
    expect(screen.getByLabelText('Ask in writing')).toBeTruthy();
  });

  it('goes back to speaking from writing', async () => {
    render(<VoiceAssistantOverlay isOpen onClose={() => undefined} />);
    await act(async () => undefined);

    fireEvent.click(screen.getByLabelText('Type instead'));
    start.mockClear();
    fireEvent.click(screen.getByLabelText('Speak instead'));

    expect(start).toHaveBeenCalledWith({ wake: false });
    expect(screen.queryByLabelText('Ask in writing')).toBeNull();
  });

  it('opens listening again next time, not stuck in the last visit’s writing mode', async () => {
    const { rerender } = render(<VoiceAssistantOverlay isOpen onClose={() => undefined} />);
    await act(async () => undefined);
    fireEvent.click(screen.getByLabelText('Type instead'));
    expect(screen.getByLabelText('Ask in writing')).toBeTruthy();

    rerender(<VoiceAssistantOverlay isOpen={false} onClose={() => undefined} />);
    rerender(<VoiceAssistantOverlay isOpen onClose={() => undefined} />);
    await act(async () => undefined);

    expect(screen.queryByLabelText('Ask in writing')).toBeNull();
    expect(screen.getByLabelText('Type instead')).toBeTruthy();
  });
});
