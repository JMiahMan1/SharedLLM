import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import SecretInput from './SecretInput';

function Harness({ saved, onReveal, onValue }: { saved: boolean; onReveal?: () => Promise<string>; onValue?: (v: string) => void }) {
  const [value, setValue] = useState('');
  return (
    <SecretInput
      label="Nextcloud Password"
      value={value}
      saved={saved}
      onReveal={onReveal}
      onChange={(v) => {
        setValue(v);
        onValue?.(v);
      }}
    />
  );
}

describe('SecretInput', () => {
  it('hides what you type until you choose to see it', async () => {
    const user = userEvent.setup();
    render(<Harness saved={false} />);
    const input = screen.getByLabelText('Nextcloud Password');
    await user.type(input, 'hunter2');
    expect(input).toHaveAttribute('type', 'password');
    await user.click(screen.getByRole('button', { name: /show nextcloud password/i }));
    expect(input).toHaveAttribute('type', 'text');
    expect(input).toHaveValue('hunter2');
  });

  it('says a value is saved, and shows it only when asked', async () => {
    const user = userEvent.setup();
    const reveal = vi.fn().mockResolvedValue('app-password-123');
    render(<Harness saved onReveal={reveal} />);
    expect(screen.getByText('Saved')).toBeInTheDocument();
    expect(reveal).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: /show nextcloud password/i }));
    expect(reveal).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('Nextcloud Password')).toHaveValue('app-password-123');
  });

  it('does not treat seeing a saved value as editing it', async () => {
    const user = userEvent.setup();
    const changes: string[] = [];
    render(<Harness saved onReveal={async () => 'abc'} onValue={(v) => changes.push(v)} />);
    await user.click(screen.getByRole('button', { name: /show nextcloud password/i }));
    expect(changes).toEqual([]);
    await user.type(screen.getByLabelText('Nextcloud Password'), 'd');
    expect(changes.at(-1)).toBe('abcd');
  });
});
