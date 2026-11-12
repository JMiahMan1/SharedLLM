import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import RepairsPanel from './RepairsPanel';
import * as automations from '../../lib/automations';

vi.mock('../../lib/automations', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return { ...real, getRemap: vi.fn(), saveRemap: vi.fn(), previewRemap: vi.fn(), suggestRemap: vi.fn() };
});
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const wrap = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <RepairsPanel onSaved={vi.fn()} />
  </QueryClientProvider>,
);

describe('RepairsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(automations.getRemap).mockResolvedValue({ remap: { entities: { 'light.a_old': 'light.a_mine' } }, report: [] });
  });

  it('finds fixes, keeps what was written by hand, shows why, and previews the result', async () => {
    vi.mocked(automations.suggestRemap).mockResolvedValue({
      remap: { entities: { 'light.a_old': 'light.a_theirs', 'switch.b_old': 'switch.b_new' } },
      evidence: [
        { old: 'switch.b_old', new: 'switch.b_new', kind: 'entity', reason: 'Same device and name' },
        { old: 'sensor.c_old', new: null, kind: 'entity', reason: 'Nothing like it on the hub' },
      ],
    });
    vi.mocked(automations.previewRemap).mockResolvedValue({ report: [{
      kind: 'automation', id: 'a1', alias: 'Porch at dusk', changes: [], dropped: [], reason_before: 'names a missing light',
      reason_after: null, enabled: false,
    }] });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /Find fixes/ }));
    const evidence = await screen.findByTestId('remap-evidence');
    expect(within(evidence).getByText('Found 2 matches:')).toBeInTheDocument();
    expect(within(evidence).getByText('No match')).toBeInTheDocument();
    await waitFor(() => expect(automations.previewRemap).toHaveBeenCalledWith({
      entities: { 'light.a_old': 'light.a_mine', 'switch.b_old': 'switch.b_new' },
    }));
    expect(await screen.findByText(/Porch at dusk/)).toBeInTheDocument();
  });

  it('keeps the raw JSON behind Advanced', async () => {
    wrap();
    const summary = await screen.findByText('Advanced: edit the remap by hand');
    expect(summary.closest('details')).not.toHaveAttribute('open');
  });
});
