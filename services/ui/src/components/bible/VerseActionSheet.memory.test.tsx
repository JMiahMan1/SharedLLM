import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import VerseActionSheet from './VerseActionSheet';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));
vi.mock('../chat/familyDirectory', () => ({ useFamilyDirectory: () => ({ byUsername: new Map(), byNextcloud: new Map() }) }));

const apiMock = vi.hoisted(() => ({
  getBlbLink: vi.fn(),
  getBibleInterlinear: vi.fn(),
  recordBibleEvent: vi.fn(),
  readNote: vi.fn(),
  rememberBibleNote: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

const VERSE = { reference: 'John 3:16', text: 'For God so loved the world', osis: 'John', chapter: 3, verse: 16 } as never;
const NOTE = { id: 7, kind: 'note', osis: 'John', chapter: 3, verse_start: 16, verse_end: null, note_path: 'Bible/John 3.md' } as never;

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getBlbLink.mockResolvedValue({ ref: 'John 3:16', tool: null, url: 'https://blb.test/' });
  apiMock.getBibleInterlinear.mockResolvedValue({ ref: 'John 3:16', available: false, attribution: null, verses: [] });
  apiMock.readNote.mockResolvedValue({ status: 'SUCCESS', message: 'Love that gives.' });
  apiMock.rememberBibleNote.mockResolvedValue({ remembered: true, id: 'bible-note-7' });
});

describe('Promoting a verse note into study memory', () => {
  it('remembers the opened note under its own id', async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <VerseActionSheet verse={VERSE} marks={[NOTE]} version="nkjv" onClose={vi.fn()} onToggleMark={vi.fn()} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await user.click(screen.getByTestId('bible-action-open-note'));
    await user.click(await screen.findByTestId('bible-note-remember'));
    await waitFor(() => expect(apiMock.rememberBibleNote).toHaveBeenCalledWith(7, 'Love that gives.', 'John 3:16'));
    expect(await screen.findByTestId('bible-sheet-status')).toHaveTextContent('Jarvis will remember this note');
  });
});
