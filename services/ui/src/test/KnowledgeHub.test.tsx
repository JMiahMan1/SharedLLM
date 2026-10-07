import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import toast from 'react-hot-toast';
import KnowledgeHub from '../pages/KnowledgeHub';
import { renderWithProviders } from './render';
import { api } from '../services/api';

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
      getStorageStatus: vi.fn().mockResolvedValue({
        status: 'SUCCESS',
        indexer: 'IDLE',
        checkpointed_files: 0,
      }),
    },
  };
});

describe('KnowledgeHub', () => {
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
    expect(screen.getByText('Indexed')).toBeInTheDocument();
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

    const indexButton = await screen.findByText('Index Folder');
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
            "No Calibre library path configured. Set the 'calibre_library_path' setting (Admin > Settings) or pass library_path with the request.",
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
});
