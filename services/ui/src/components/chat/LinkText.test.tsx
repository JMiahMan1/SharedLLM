import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import LinkText from './LinkText';

describe('LinkText', () => {
  it('makes web addresses tappable and leaves the rest as text', () => {
    render(<LinkText text="Look at https://example.com/a?b=1, then www.not-a-link" />);
    const link = screen.getByTestId('private-link');
    expect(link).toHaveAttribute('href', 'https://example.com/a?b=1');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getAllByTestId('private-link')).toHaveLength(1);
    expect(document.body.textContent).toContain(', then www.not-a-link');
  });

  it('a tap on a link opens it without also opening the message', () => {
    const onBubble = vi.fn();
    render(
      <div onClick={onBubble}>
        <LinkText text="see http://example.org." />
      </div>,
    );
    const link = screen.getByTestId('private-link');
    expect(link).toHaveAttribute('href', 'http://example.org');
    fireEvent.click(link);
    expect(onBubble).not.toHaveBeenCalled();
  });

  it('text without links is unchanged', () => {
    render(<LinkText text="just words" />);
    expect(screen.queryByTestId('private-link')).toBeNull();
    expect(document.body.textContent).toBe('just words');
  });
});
