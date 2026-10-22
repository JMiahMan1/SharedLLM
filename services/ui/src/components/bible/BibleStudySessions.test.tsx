import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import BibleStudySessions from './BibleStudySessions';

const apiMock = vi.hoisted(() => ({
  listBibleStudySessions: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

function renderSessions(reference: string) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BibleStudySessions reference={reference} version="nkjv" onOpenRef={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiMock.listBibleStudySessions.mockReset();
  apiMock.listBibleStudySessions.mockResolvedValue({ sessions: [] });
});

describe('Bible study sessions', () => {
  it('names the passage the study will open', async () => {
    renderSessions('John 3');

    const button = await screen.findByRole('button', { name: 'Start a Bible study on John 3' });
    expect(button).toHaveTextContent('Study John 3');
    expect(button).toBeEnabled();
  });

  it('asks for a passage instead of offering to study "Reading"', async () => {
    renderSessions('Reading');

    const button = await screen.findByRole('button', { name: 'Start a Bible study' });
    expect(button).toHaveTextContent('Start a study');
    expect(button).toBeDisabled();
    expect(screen.queryByText(/Study Reading/)).not.toBeInTheDocument();
  });
});
