import { describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import Sheet from './Sheet';

/** The chat re-renders on every poll and hands the sheet a new inline onClose each time. */
function PollingChat({ onClose }: { onClose: () => void }) {
  const [, setTick] = useState(0);
  const [name, setName] = useState('');
  return (
    <>
      <button type="button" data-testid="poll" onClick={() => setTick((t) => t + 1)}>poll</button>
      <Sheet title="New chat" onClose={() => onClose()}>
        <input aria-label="Group name" value={name} onChange={(e) => setName(e.target.value)} />
      </Sheet>
    </>
  );
}

describe('chat Sheet', () => {
  it('keeps the field being typed in focused while the chat behind it polls', () => {
    render(<PollingChat onClose={() => undefined} />);
    const field = screen.getByLabelText('Group name') as HTMLInputElement;
    field.focus();
    fireEvent.change(field, { target: { value: 'Camp' } });

    // A poll lands: the parent re-renders with a fresh onClose.
    act(() => screen.getByTestId('poll').click());
    act(() => screen.getByTestId('poll').click());

    expect(document.activeElement).toBe(field);
    fireEvent.change(field, { target: { value: 'Camping' } });
    expect(field.value).toBe('Camping');
  });

  it('still closes on Escape with the newest onClose', () => {
    const onClose = vi.fn();
    render(<PollingChat onClose={onClose} />);
    act(() => screen.getByTestId('poll').click());
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
