import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError, AxiosHeaders } from 'axios';
import WindowsGuardPanel from './WindowsGuardPanel';
import { installerDownloads, sizeLabel } from '../../lib/windowsInstaller';
import { api } from '../../services/api';
import type { WindowsInstallerInfo } from '../../types/api';

vi.mock('../../services/api', () => ({
  api: { getWindowsInstaller: vi.fn(), getWindowsInstallerFile: vi.fn() },
}));
vi.mock('../../lib/artifactKinds', () => ({ downloadBlobUrl: vi.fn() }));

const INFO: WindowsInstallerInfo = {
  available: true,
  version: '0.3.0',
  sha256: 'ab'.repeat(32),
  git_sha: '0123abcd',
  built_at: '2026-01-05T10:00:00Z',
  files: [
    { name: 'jarvis-guard-windows.zip', size: 46 * 1024 * 1024 },
    { name: 'jarvis-guard.exe', size: 52 * 1024 * 1024 },
    { name: 'install.ps1', size: 9000 },
  ],
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><WindowsGuardPanel /></QueryClientProvider>);
}

function refused(status: number) {
  const headers = new AxiosHeaders();
  return new AxiosError('refused', String(status), undefined, undefined,
    { status, statusText: '', headers, config: { headers }, data: {} });
}

describe('installer helpers', () => {
  it('offers the one zip when the server has it, else the files', () => {
    expect(installerDownloads(INFO)).toEqual(['jarvis-guard-windows.zip']);
    expect(installerDownloads({ ...INFO, files: INFO.files.slice(1) })).toEqual(['jarvis-guard.exe', 'install.ps1']);
  });

  it('says sizes in words a parent reads', () => {
    expect(sizeLabel(52 * 1024 * 1024)).toBe('52 MB');
    expect(sizeLabel(9000)).toBe('9 KB');
  });
});

describe('WindowsGuardPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the version, the hash and the steps, and downloads with the key', async () => {
    vi.mocked(api.getWindowsInstaller).mockResolvedValue(INFO);
    vi.mocked(api.getWindowsInstallerFile).mockResolvedValue(new Blob(['zip']));
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:fake');
    renderPanel();
    expect(await screen.findByTestId('windows-guard-version')).toHaveTextContent('Jarvis Guard 0.3.0');
    expect(screen.getByTestId('windows-guard-sha256')).toHaveTextContent('ab'.repeat(32));
    expect(screen.getByText(/ExecutionPolicy Bypass/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Download installer/ }));
    await waitFor(() => expect(api.getWindowsInstallerFile).toHaveBeenCalledWith('jarvis-guard-windows.zip'));
  });

  it('says when nothing is on the server yet', async () => {
    vi.mocked(api.getWindowsInstaller).mockResolvedValue({ ...INFO, available: false, version: null, sha256: null, files: [] });
    renderPanel();
    expect(await screen.findByTestId('windows-guard-missing')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Download/ })).toBeNull();
  });

  it('is not shown to someone who does not manage devices', async () => {
    vi.mocked(api.getWindowsInstaller).mockRejectedValue(refused(403));
    const { container } = renderPanel();
    await waitFor(() => expect(api.getWindowsInstaller).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector('[data-testid="windows-guard-panel"]')).toBeNull());
  });
});
