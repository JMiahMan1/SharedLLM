import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import SendAsSelector, { readSendAsPref, useSendAsPref } from './SendAsSelector';

function Harness({ isAdmin }: { isAdmin: boolean }) {
  const [sendAs, setSendAs] = useSendAsPref(isAdmin);
  return <SendAsSelector value={sendAs} onChange={setSendAs} />;
}

describe('SendAsSelector', () => {
  beforeEach(() => localStorage.clear());

  it('defaults to the caller own identity', () => {
    expect(readSendAsPref()).toBe('me');
    localStorage.setItem('jarvis-talk-send-as', 'admin');
    expect(readSendAsPref()).toBe('admin');
  });

  it('renders both choices with Me selected and no badge by default', () => {
    render(<SendAsSelector value="me" onChange={() => undefined} />);

    const radios = screen.getAllByRole('radio');
    expect(radios.length).toBe(2);
    expect(screen.getByRole('radio', { name: /me/i }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('radio', { name: /admin/i }).getAttribute('aria-checked')).toBe('false');
    expect(screen.queryByTestId('send-as-badge')).toBeNull();
  });

  it('reports the admin choice and shows the active badge', () => {
    const onChange = vi.fn();
    render(<SendAsSelector value="me" onChange={onChange} />);

    fireEvent.click(screen.getByRole('radio', { name: /admin/i }));
    expect(onChange).toHaveBeenCalledWith('admin');
  });

  it('badges the Admin identity once selected', () => {
    render(<SendAsSelector value="admin" onChange={() => undefined} />);
    expect(screen.getByTestId('send-as-badge').textContent).toContain('Sending as Admin');
  });

  it('keeps a non-admin pinned to their own identity even if a preference was stored', () => {
    localStorage.setItem('jarvis-talk-send-as', 'admin');
    render(<Harness isAdmin={false} />);

    expect(screen.getByRole('radio', { name: /me/i }).getAttribute('aria-checked')).toBe('true');
    expect(screen.queryByTestId('send-as-badge')).toBeNull();
  });

  it('lets an admin switch to Admin and persists the choice', () => {
    render(<Harness isAdmin />);

    fireEvent.click(screen.getByRole('radio', { name: /admin/i }));
    expect(screen.getByTestId('send-as-badge')).toBeTruthy();
    expect(localStorage.getItem('jarvis-talk-send-as')).toBe('admin');
  });
});
