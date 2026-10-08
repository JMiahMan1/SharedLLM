import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SystemValueHint from './SystemValueHint';

describe('SystemValueHint', () => {
  it('quietly says the value comes from the system config when it matches', () => {
    render(<SystemValueHint value="https://cloud.example" systemValue="https://cloud.example" onReset={() => {}} />);
    expect(screen.getByText(/from system config/i)).toBeInTheDocument();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('warns once the value is changed, naming the system value, and resets on request', async () => {
    const user = userEvent.setup();
    const reset = vi.fn();
    render(<SystemValueHint value="https://other.example" systemValue="https://cloud.example" onReset={reset} />);
    expect(screen.getByRole('status')).toHaveTextContent(/differs from the system config \(https:\/\/cloud\.example\)/i);
    await user.click(screen.getByRole('button', { name: /reset to system value/i }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it('names the admin account when the value came from there instead', () => {
    render(<SystemValueHint value="x" systemValue="https://ha.example" source="default_user" onReset={() => {}} />);
    expect(screen.getByRole('status')).toHaveTextContent(/household admin account/i);
  });
});
