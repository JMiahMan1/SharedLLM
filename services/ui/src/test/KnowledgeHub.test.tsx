import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
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
      getRagStats: vi.fn(),
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
});
