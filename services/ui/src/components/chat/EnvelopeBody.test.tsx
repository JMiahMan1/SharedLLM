import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import EnvelopeBody from './EnvelopeBody';
import { encodeEnvelope } from '../../lib/chatEnvelope';

describe('EnvelopeBody', () => {
  it('renders plain text with no card', () => {
    render(<EnvelopeBody raw="dinner is ready" />);
    expect(screen.getByText('dinner is ready')).toBeTruthy();
    expect(screen.queryByTestId('envelope-activity')).toBeNull();
  });

  it('renders an activity card with stats and stars', () => {
    const raw = encodeEnvelope('Nice one!', {
      kind: 'activity',
      title: 'First 10k steps',
      detail: 'Mom hit today’s goal',
      stars: 2,
      stats: [{ label: 'Steps', value: '10,240' }],
    });
    render(<EnvelopeBody raw={raw} />);

    const card = screen.getByTestId('envelope-activity');
    expect(card.textContent).toContain('First 10k steps');
    expect(card.textContent).toContain('Mom hit today');
    expect(card.textContent).toContain('10,240');
    expect(card.textContent).toContain('2');
  });

  it('keeps the readable text above the card', () => {
    const raw = encodeEnvelope('Quiz night winner!', { kind: 'game', title: 'Family trivia', stars: 1 });
    render(<EnvelopeBody raw={raw} />);
    expect(screen.getByText('Quiz night winner!')).toBeTruthy();
    expect(screen.getByTestId('envelope-game')).toBeTruthy();
  });

  it('offers the action link when the card carries one', () => {
    const raw = encodeEnvelope('Have a go', {
      kind: 'game',
      title: 'Space Invaders',
      action: { label: 'Play', href: 'http://arcade.local/play/space-invaders' },
    });
    render(<EnvelopeBody raw={raw} />);

    const link = screen.getByRole('link', { name: 'Play' });
    expect(link.getAttribute('href')).toBe('http://arcade.local/play/space-invaders');
  });

  it('falls back to the system message when the body is empty', () => {
    render(<EnvelopeBody raw="" fallback="Mom joined the call" />);
    expect(screen.getByText('Mom joined the call')).toBeTruthy();
  });

  it('shows text only when the card is corrupt', () => {
    const raw = 'still fine\n\n```jarvis-envelope\n{broken\n```jarvis-envelope';
    render(<EnvelopeBody raw={raw} />);
    expect(screen.getByText(/still fine/)).toBeTruthy();
    expect(screen.queryByTestId(/envelope-/)).toBeNull();
  });
});
