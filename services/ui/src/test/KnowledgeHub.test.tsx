import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import toast from 'react-hot-toast';
import KnowledgeHub from '../pages/KnowledgeHub';
import { renderWithProviders } from './render';
import { api } from '../services/api';

const authState = { user: { username: 'jeremiah', is_admin: true } as unknown };
vi.mock('../context/AuthContext', async (importOriginal) => ({
  ...(await importOriginal() as Record<string, unknown>),
  useAuth: () => authState,
}));

// Mock the API methods
vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getStorageFiles: vi.fn(),
      triggerIndexing: vi.fn(),
      triggerFullIndex: vi.fn().mockResolvedValue({ status: 'ACCEPTED', message: 'Indexing started in background.' }),
      getRagStats: vi.fn(),
      globalSearch: vi.fn(),
      purgeRagCollection: vi.fn(),
      getRavenLearnings: vi.fn().mockResolvedValue({ status: 'SUCCESS', count: 0, items: [] }),
      getStorageStatus: vi.fn().mockResolvedValue({
        status: 'SUCCESS',
        indexer: 'IDLE',
        checkpointed_files: 0,
      }),
    },
  };
});

describe('KnowledgeHub', () => {
  beforeEach(() => {
    authState.user = { username: 'jeremiah', is_admin: true } as unknown;
    vi.mocked(api.getRagStats).mockResolvedValue({ total_chunks: 0, total_documents: 0 });
    vi.mocked(api.getStorageFiles).mockResolvedValue([]);
  });

  it('renders RAG stats and file explorer', async () => {
    vi.mocked(api.getRagStats).mockResolvedValue({
      total_chunks: 5000,
      total_documents: 100,
      last_indexed: '2026-05-06T10:00:00Z',
      providers: ['nextcloud']
    });
    vi.mocked(api.getStorageFiles).mockResolvedValue([
      { path: '/Notes', name: 'Notes', is_dir: true, size: null, indexed: false },
      { path: '/resume.pdf', name: 'resume.pdf', is_dir: false, size: 2048, indexed: true },
    ]);

    renderWithProviders(<KnowledgeHub />);

    expect(screen.getByText('Knowledge Hub')).toBeInTheDocument();
    
    await waitFor(() => {
      expect(screen.getByText('5,000')).toBeInTheDocument();
      expect(screen.getByText('100')).toBeInTheDocument();
    });

    expect(screen.getByText('Notes')).toBeInTheDocument();
    expect(screen.getByText('resume.pdf')).toBeInTheDocument();
    expect(screen.getAllByText('Indexed').length).toBeGreaterThan(0);
    // The status badge reflects the stats request, not a fixed label.
    expect(screen.getByTestId('rag-status')).toHaveTextContent('Online');
  });

  it('handles navigation', async () => {
    vi.mocked(api.getStorageFiles).mockResolvedValueOnce([
      { path: '/Notes', name: 'Notes', is_dir: true, size: null, indexed: false },
    ]);
    vi.mocked(api.getStorageFiles).mockResolvedValueOnce([
      { path: '/Notes/Secret', name: 'Secret', is_dir: true, size: null, indexed: false },
    ]);

    renderWithProviders(<KnowledgeHub />);

    const notesFolder = await screen.findByText('Notes');
    fireEvent.click(notesFolder);

    await waitFor(() => {
      expect(api.getStorageFiles).toHaveBeenCalledWith('/Notes');
      expect(screen.getByText('Secret')).toBeInTheDocument();
    });

    // Wait, let's look for the breadcrumb "Root"
    const rootBreadcrumb = screen.getByText('Root');
    fireEvent.click(rootBreadcrumb);

    await waitFor(() => {
       expect(api.getStorageFiles).toHaveBeenCalledWith('/');
    });
  });

  it('triggers indexing when button is clicked', async () => {
    vi.mocked(api.getStorageFiles).mockResolvedValue([
      { path: '/Notes', name: 'Notes', is_dir: true, size: null, indexed: false },
    ]);
    vi.mocked(api.triggerIndexing).mockResolvedValue({ status: 'ACCEPTED', message: 'Started' });

    renderWithProviders(<KnowledgeHub />);

    const indexButton = await screen.findByRole('button', { name: 'Index folder Notes' });
    fireEvent.click(indexButton);

    await waitFor(() => {
      expect(api.triggerIndexing).toHaveBeenCalledWith('/Notes', true);
    });
  });

  it('starts a Calibre library crawl when that library is selected', async () => {
    renderWithProviders(<KnowledgeHub />);

    fireEvent.click(await screen.findByRole('button', { name: 'Calibre library' }));

    // The card must describe the provider that is actually selected.
    expect(await screen.findByText('Reindex the Calibre Library')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Start Full Reindex/ }));

    await waitFor(() => {
      expect(api.triggerFullIndex).toHaveBeenCalledWith({ kind: 'calibre' }, { force: false });
    });
  });

  it('keeps the NextCloud reindex as the default provider', async () => {
    renderWithProviders(<KnowledgeHub />);

    fireEvent.click(await screen.findByRole('button', { name: /Start Full Reindex/ }));

    await waitFor(() => {
      expect(api.triggerFullIndex).toHaveBeenCalledWith({ kind: 'nextcloud' }, { force: false });
    });
  });

  it('shows live crawl progress while a crawl is running', async () => {
    vi.mocked(api.getStorageStatus).mockResolvedValue({
      status: 'SUCCESS',
      indexer: 'IDLE',
      checkpointed_files: 0,
      crawl: { active: true, kind: 'calibre', phase: 'extracting', done: 120, total: 3079 },
    });

    renderWithProviders(<KnowledgeHub />);

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('Crawling calibre');
    expect(status).toHaveTextContent('Extracting text');
    expect(status).toHaveTextContent('120/3079');
  });

  it('surfaces the gateway detail when the crawl is refused', async () => {
    vi.mocked(api.triggerFullIndex).mockRejectedValueOnce({
      response: {
        data: {
          detail:
            "No Calibre library path configured. Set the 'calibre_library_path' setting (Admin > LLM & Settings) or pass library_path with the request.",
        },
      },
    });
    const errorSpy = vi.spyOn(toast, 'error').mockImplementation(() => 'id');

    renderWithProviders(<KnowledgeHub />);
    fireEvent.click(await screen.findByRole('button', { name: 'Calibre library' }));
    fireEvent.click(screen.getByRole('button', { name: /Start Full Reindex/ }));

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining('calibre_library_path'));
    });
    errorSpy.mockRestore();
  });

  it('says the index is unavailable when the stats request fails', async () => {
    vi.mocked(api.getRagStats).mockRejectedValue(new Error('down'));
    renderWithProviders(<KnowledgeHub />);
    await waitFor(() => expect(screen.getByTestId('rag-status')).toHaveTextContent('Unavailable'));
  });

  it('filters the folder listing as you type', async () => {
    vi.mocked(api.getStorageFiles).mockResolvedValue([
      { path: '/Notes', name: 'Notes', is_dir: true, size: null, indexed: false },
      { path: '/Budget.xlsx', name: 'Budget.xlsx', is_dir: false, size: 2048, indexed: false },
    ]);
    renderWithProviders(<KnowledgeHub />);
    await screen.findByText('Budget.xlsx');

    fireEvent.change(screen.getByLabelText('Filter files in this folder'), { target: { value: 'budg' } });

    expect(screen.queryByText('Notes')).not.toBeInTheDocument();
    expect(screen.getByText('Budget.xlsx')).toBeInTheDocument();
  });

  it('shows why a folder cannot be listed instead of calling it empty', async () => {
    vi.mocked(api.getStorageFiles).mockRejectedValue({
      response: { data: { detail: 'NextCloud credentials not configured for this user.' } },
    });
    renderWithProviders(<KnowledgeHub />);
    expect(await screen.findByRole('alert')).toHaveTextContent('NextCloud credentials not configured');
  });

  it('shows each search hit with its own text and where it came from', async () => {
    vi.mocked(api.globalSearch).mockResolvedValue({
      answer: 'Kitchen light is in the kitchen',
      files: [
        { name: 'Kitchen', path: 'light.kitchen', collection: 'ha_entities', snippet: 'Kitchen light is in the kitchen' },
        { name: 'Budget.md', path: '/Budget.md', collection: 'nextcloud_files', snippet: 'Groceries 400' },
      ],
    });
    renderWithProviders(<KnowledgeHub />);
    fireEvent.change(screen.getByLabelText('Semantic memory search'), { target: { value: 'kitchen' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));

    expect(await screen.findByText('Groceries 400')).toBeInTheDocument();
    expect(screen.getByText('Home Assistant')).toBeInTheDocument();
    // The answer is the first hit's text: it is not shown twice.
    expect(screen.getAllByText('Kitchen light is in the kitchen')).toHaveLength(1);
  });

  it('only offers lesson Edit/Delete to admins', async () => {
    vi.mocked(api.getRavenLearnings).mockResolvedValue({
      status: 'SUCCESS',
      count: 1,
      items: [{
        id: 'l1', content: 'Rename master to main', metadata: { topic: 'git' }, created_at: 1,
        usage_count: 0, applied_count: 0,
      }],
    } as never);
    authState.user = { username: 'michele', is_admin: false } as unknown;
    renderWithProviders(<KnowledgeHub />);
    fireEvent.click(await screen.findByText('git'));
    expect(screen.getByText('Rename master to main')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Delete/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Edit/ })).not.toBeInTheDocument();
  });

  it('says so when a purge matched nothing', async () => {
    vi.mocked(api.purgeRagCollection).mockResolvedValue({ status: 'SUCCESS', message: '', removed: 0 });
    const toastSpy = vi.spyOn(toast, 'success');
    renderWithProviders(<KnowledgeHub />);
    fireEvent.click(await screen.findByRole('button', { name: 'Purge Nextcloud Data' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm Purge' }));

    await waitFor(() => expect(api.purgeRagCollection).toHaveBeenCalledWith('nextcloud_files'));
    expect(toastSpy).not.toHaveBeenCalled();
    toastSpy.mockRestore();
  });
});
