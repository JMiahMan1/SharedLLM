import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import Toggle from '../components/ui/Toggle';

describe('Toggle', () => {
  it('exposes switch role and aria-checked', () => {
    render(<Toggle checked={false} onChange={() => undefined} ariaLabel="Demo" />);
    const el = screen.getByRole('switch', { name: 'Demo' });
    expect(el).toHaveAttribute('aria-checked', 'false');
  });

  it('calls onChange with next value', () => {
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} ariaLabel="Demo" />);
    fireEvent.click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it('does not fire when disabled', () => {
    const onChange = vi.fn();
    render(<Toggle checked onChange={onChange} disabled ariaLabel="Demo" />);
    fireEvent.click(screen.getByRole('switch'));
    expect(onChange).not.toHaveBeenCalled();
  });
});
