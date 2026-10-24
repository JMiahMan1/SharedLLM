import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import toast from 'react-hot-toast';
import Notes from '../pages/Notes';
import { renderWithProviders } from './render';
import { notesWriteCalls } from './setup';

describe('Notes keyboard shortcuts', () => {
  beforeEach(() => {
    localStorage.clear();
    toast.remove();
  });

  it('C takes a note and / jumps to the notes search', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);
    await screen.findByText('Trip ideas');

    await user.keyboard('/');
    expect(screen.getByLabelText('Search notes')).toHaveFocus();

    // Typing in the search box is typing, not a shortcut.
    await user.keyboard('c');
    expect(screen.getByLabelText('Search notes')).toHaveValue('c');
    expect(screen.queryByLabelText('Note title')).toBeNull();

    // Escape leaves the box; now C is a shortcut again.
    await user.keyboard('{Escape}');
    await user.keyboard('c');
    expect(await screen.findByLabelText('Note title')).toHaveValue('');
  });

  it('ignores a held modifier (Ctrl+C stays copy)', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);
    await screen.findByText('Trip ideas');

    await user.keyboard('{Control>}c{/Control}');
    expect(screen.queryByLabelText('Note title')).toBeNull();
  });

  it('Ctrl+S saves from inside a field and Ctrl+Enter saves and closes', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);
    await screen.findByText('Trip ideas');

    await user.click(screen.getByTestId('quick-capture'));
    await user.type(screen.getByLabelText('Note title'), 'Errands');
    // The letter C inside the editor is just a letter.
    expect(screen.getByLabelText('Note title')).toHaveValue('Errands');
    await user.keyboard('{Control>}s{/Control}');
    await waitFor(() => expect(notesWriteCalls.length).toBe(1));
    expect(notesWriteCalls[0]).toMatchObject({ title: 'Errands' });
    await waitFor(() => expect(screen.queryByLabelText('Note title')).toBeNull());

    await user.click(await screen.findByRole('heading', { name: 'Trip ideas' }));
    const content = await screen.findByLabelText('Note content');
    await user.type(content, ' More.');
    await user.keyboard('{Meta>}{Enter}{/Meta}');
    await waitFor(() => expect(notesWriteCalls.length).toBe(2));
    expect(notesWriteCalls[1].content).toContain('More.');
    await waitFor(() => expect(screen.queryByLabelText('Note content')).toBeNull());
  });
});
