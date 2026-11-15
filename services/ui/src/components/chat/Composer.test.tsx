import { describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import Composer from './Composer';
import { isImeKey } from '../../lib/keyboard';

vi.mock('../../services/api', () => ({ api: { getTalkMentions: vi.fn().mockResolvedValue({ detail: { mentions: [] } }) } }));

function Chat({ onSend = () => undefined }: { onSend?: (text: string) => void }) {
  const [token, setToken] = useState('room-a');
  const [drafts] = useState(() => new Map<string, string>());
  return (
    <>
      <button type="button" onClick={() => setToken('room-a')}>Open A</button>
      <button type="button" onClick={() => setToken('room-b')}>Open B</button>
      <Composer
        token={token}
        drafts={drafts}
        disabled={false}
        replyTo={null}
        onCancelReply={() => undefined}
        editing={null}
        onCancelEdit={() => undefined}
        onSaveEdit={() => undefined}
        onSend={onSend}
        onSendVoice={async () => true}
        sendingVoice={false}
        staged={[]}
        onStage={() => undefined}
        onUnstage={() => undefined}
        onSendFiles={async () => true}
        sendingFiles={false}
        onNewPoll={() => undefined}
        isAdmin={false}
        sendAs="me"
        onSendAsChange={() => undefined}
      />
    </>
  );
}

const box = () => screen.getByLabelText('Message') as HTMLTextAreaElement;

describe('Composer', () => {
  it('keeps each conversation\'s unsent text with that conversation', () => {
    render(<Chat />);
    fireEvent.change(box(), { target: { value: 'for the first room' } });

    fireEvent.click(screen.getByText('Open B'));
    // The text written for A never shows up, or gets sent, in B.
    expect(box().value).toBe('');
    fireEvent.change(box(), { target: { value: 'for the second room' } });

    fireEvent.click(screen.getByText('Open A'));
    expect(box().value).toBe('for the first room');
    fireEvent.click(screen.getByText('Open B'));
    expect(box().value).toBe('for the second room');
  });

  it('sends on Enter, but not on an Enter the keyboard is still using for a word', () => {
    const onSend = vi.fn();
    render(<Chat onSend={onSend} />);
    fireEvent.change(box(), { target: { value: 'see you at six' } });

    fireEvent.keyDown(box(), { key: 'Enter', keyCode: 229 });
    fireEvent.keyDown(box(), { key: 'Enter', keyCode: 13, isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
    expect(box().value).toBe('see you at six');

    fireEvent.keyDown(box(), { key: 'Enter', keyCode: 13 });
    expect(onSend).toHaveBeenCalledWith('see you at six');
    expect(box().value).toBe('');
  });
});

describe('isImeKey', () => {
  it('is true while a word is composed and for the 229 key code', () => {
    expect(isImeKey({ keyCode: 13, nativeEvent: { isComposing: true } })).toBe(true);
    expect(isImeKey({ keyCode: 229, nativeEvent: { isComposing: false } })).toBe(true);
    expect(isImeKey({ keyCode: 13, nativeEvent: { isComposing: false } })).toBe(false);
  });
});
