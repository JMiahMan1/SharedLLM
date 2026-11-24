import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import PasswordField from './PasswordField';
import AdoptDeviceDialog from '../devices/AdoptDeviceDialog';

function Harness() {
  const [value, setValue] = useState('');
  return (
    <div>
      <label htmlFor="pw-test">Web password</label>
      <PasswordField id="pw-test" value={value} onChange={(e) => setValue(e.target.value)} />
    </div>
  );
}

describe('PasswordField', () => {
  it('hides the password until the eye is pressed, and hides it again', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText('Web password');
    await user.type(input, 'made-up-pass');
    expect(input).toHaveAttribute('type', 'password');
    const eye = screen.getByRole('button', { name: 'Show password' });
    expect(eye).toHaveAttribute('aria-pressed', 'false');
    await user.click(eye);
    expect(input).toHaveAttribute('type', 'text');
    expect(input).toHaveValue('made-up-pass');
    const hide = screen.getByRole('button', { name: 'Hide password' });
    expect(hide).toHaveAttribute('aria-pressed', 'true');
    await user.click(hide);
    expect(input).toHaveAttribute('type', 'password');
  });

  it('works from the keyboard', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText('Web password');
    await user.click(input);
    await user.tab();
    expect(screen.getByRole('button', { name: 'Show password' })).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(input).toHaveAttribute('type', 'text');
    await user.keyboard(' ');
    expect(input).toHaveAttribute('type', 'password');
  });

  it('never writes the value to the console', async () => {
    const user = userEvent.setup();
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) => vi.spyOn(console, m));
    render(<Harness />);
    await user.type(screen.getByLabelText('Web password'), 'made-up-pass');
    await user.click(screen.getByRole('button', { name: 'Show password' }));
    for (const spy of spies) {
      expect(spy.mock.calls.flat().join(' ')).not.toContain('made-up-pass');
      spy.mockRestore();
    }
  });

  it('gives every password field a device flow asks for an eye, and none to plain fields', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue({ status: 'adopted' });
    render(
      <AdoptDeviceDialog
        title="Add Test Lamp"
        needs={[
          { key: 'password', label: 'Web password', type: 'password' },
          { key: 'host', label: 'Address', type: 'text', optional: true },
        ]}
        onSubmit={onSubmit}
        onDone={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const input = screen.getByLabelText(/Web password/);
    expect(input).toHaveAttribute('type', 'password');
    expect(screen.getAllByRole('button', { name: 'Show password' })).toHaveLength(1);
    await user.type(input, 'made-up-pass');
    await user.click(screen.getByRole('button', { name: 'Show password' }));
    expect(input).toHaveAttribute('type', 'text');
    await user.click(screen.getByRole('button', { name: 'Add' }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ input: expect.objectContaining({ password: 'made-up-pass' }) }));
  });
});
