import { describe, expect, it, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ThemePackageManager } from './ThemePackageManager';
import { themeRegistry } from './registry';
import { renderWithProviders } from '../test/render';
import {
  createThemePackage,
  createThemePack,
  parseThemePackJson,
  serializeThemePack,
} from './types';

function seedUserPack(id = 'ui-user-pack') {
  const pack = createThemePack(id, 'UI User Pack');
  pack.themes = [
    createThemePackage(`${id}-theme`, 'Custom', {
      bg: '#0B1020',
      text: '#E2E8F0',
      accent: '#22D3EE',
    }),
  ];
  themeRegistry.importPackJson(serializeThemePack(pack));
  return pack;
}

describe('ThemePackageManager (management UI)', () => {
  beforeEach(() => {
    localStorage.clear();
    themeRegistry.reload();
    vi.restoreAllMocks();
    if (typeof window.prompt !== 'function') {
      vi.stubGlobal('prompt', vi.fn());
    }
    if (typeof window.confirm !== 'function') {
      vi.stubGlobal('confirm', vi.fn(() => true));
    }
  });

  it('lists builtin pack themes without hardcoding palettes', async () => {
    renderWithProviders(<ThemePackageManager />);
    expect((await screen.findAllByText(/Jarvis Default/)).length).toBeGreaterThan(0);
    expect(screen.getByText('Aurora')).toBeInTheDocument();
    expect(screen.getByText('Tron')).toBeInTheDocument();
    expect(screen.getByText('Neon')).toBeInTheDocument();
  });

  it('selects a theme via onSelectTheme callback', async () => {
    const onSelect = vi.fn();
    renderWithProviders(
      <ThemePackageManager selectedThemeId="aurora" onSelectTheme={onSelect} />
    );
    const user = userEvent.setup();
    const tronButton = await screen.findByRole('button', { name: /tron/i });
    await user.click(tronButton);
    expect(onSelect).toHaveBeenCalledWith('tron');
  });

  it('imports a pack from pasted JSON', async () => {
    const pack = createThemePack('pasted-pack', 'Pasted Pack');
    pack.themes = [
      createThemePackage('pasted-theme', 'Pasted', {
        bg: '#101010',
        text: '#fafafa',
        accent: '#ff00aa',
      }),
    ];
    const json = serializeThemePack(pack);

    renderWithProviders(<ThemePackageManager />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /import/i }));
    const textarea = await screen.findByRole('textbox');
    fireEvent.change(textarea, { target: { value: json } });
    await user.click(screen.getByRole('button', { name: /install pack/i }));

    await waitFor(() => {
      expect(themeRegistry.getPack('pasted-pack')).toBeTruthy();
    });
    expect(screen.getByText(/Imported pack/i)).toBeInTheDocument();
    expect(screen.getByText('Pasted Pack')).toBeInTheDocument();
  });

  it('rejects invalid import JSON with an error message', async () => {
    renderWithProviders(<ThemePackageManager />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /import/i }));
    const textarea = await screen.findByRole('textbox');
    fireEvent.change(textarea, { target: { value: '{ not valid pack' } });
    await user.click(screen.getByRole('button', { name: /install pack/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/./);
    expect(themeRegistry.listPacks().map((p) => p.id)).not.toContain('not-valid');
  });

  it('creates a new user pack from the schematic', async () => {
    renderWithProviders(<ThemePackageManager />);
    const user = userEvent.setup();

    vi.mocked(window.prompt)
      .mockReturnValueOnce('new-ui-pack')
      .mockReturnValueOnce('New UI Pack');

    await user.click(await screen.findByRole('button', { name: /new pack/i }));

    const created = themeRegistry.getPack('new-ui-pack');
    expect(created).toBeTruthy();
    expect(created?.builtin).not.toBe(true);
    expect(created?.themes.length).toBeGreaterThan(0);
  });

  it('exports an installed pack as valid schematic JSON', async () => {
    seedUserPack('export-me');
    renderWithProviders(<ThemePackageManager />);
    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal(
      'URL',
      Object.assign(Object.create(URL), { createObjectURL, revokeObjectURL })
    );

    const user = userEvent.setup();
    const exportButtons = await screen.findAllByTitle('Export pack JSON');
    expect(exportButtons.length).toBeGreaterThan(0);
    await user.click(exportButtons[exportButtons.length - 1]);

    expect(createObjectURL).toHaveBeenCalled();
    const json = themeRegistry.exportPack('export-me');
    const parsed = parseThemePackJson(json);
    expect(parsed.id).toBe('export-me');
    vi.unstubAllGlobals();
  });

  it('removes a user pack but refuses to remove builtin', async () => {
    seedUserPack('remove-me');
    vi.mocked(window.confirm).mockReturnValue(true);
    renderWithProviders(<ThemePackageManager />);
    const user = userEvent.setup();

    const removeBtn = await screen.findByTitle('Remove pack');
    await user.click(removeBtn);

    expect(themeRegistry.getPack('remove-me')).toBeUndefined();
    expect(themeRegistry.getPack('jarvis-default')).toBeTruthy();
  });

  it('disables and re-enables a theme without deleting the pack', async () => {
    renderWithProviders(<ThemePackageManager />);
    const user = userEvent.setup();

    const disableBtns = await screen.findAllByTitle('Disable theme');
    await user.click(disableBtns[0]);
    expect(themeRegistry.isThemeEnabled('aurora')).toBe(false);
    expect(themeRegistry.getPack('jarvis-default')).toBeTruthy();

    const enableBtns = await screen.findAllByTitle('Enable theme');
    await user.click(enableBtns[0]);
    expect(themeRegistry.isThemeEnabled('aurora')).toBe(true);
    expect(themeRegistry.getPack('jarvis-default')).toBeTruthy();
  });
});
