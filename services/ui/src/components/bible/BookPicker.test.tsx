import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import BookPicker from './BookPicker';

const BOOKS = [
  { osis: 'Gen', name: 'Genesis', order: 1, chapters: 50 },
  { osis: 'Obad', name: 'Obadiah', order: 31, chapters: 1 },
  { osis: 'John', name: 'John', order: 43, chapters: 21 },
];

describe('book picker', () => {
  it('goes from a book to a chapter in two taps', async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    render(<BookPicker books={BOOKS} current="Gen" onPick={onPick} onClose={vi.fn()} />);
    expect(screen.getByText('Old Testament')).toBeInTheDocument();
    expect(screen.getByText('New Testament')).toBeInTheDocument();
    await user.click(screen.getByTestId('bible-pick-John'));
    expect(screen.getByTestId('bible-chapter-grid').children).toHaveLength(21);
    await user.click(screen.getByRole('button', { name: '3' }));
    expect(onPick).toHaveBeenCalledWith('John', 3);
  });

  it('opens a one-chapter book straight away', async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    render(<BookPicker books={BOOKS} current="Gen" onPick={onPick} onClose={vi.fn()} />);
    await user.click(screen.getByTestId('bible-pick-Obad'));
    expect(onPick).toHaveBeenCalledWith('Obad', 1);
  });
});
