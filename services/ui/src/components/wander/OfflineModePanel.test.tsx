import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (orig) => {
  const real = await orig<typeof import('@capacitor/core')>();
  return { ...real, Capacitor: { ...real.Capacitor, getPlatform: () => platform.name } };
});
const plugin = vi.hoisted(() => ({
  queue: vi.fn(), requests: vi.fn(), cache: vi.fn(), sendCommand: vi.fn(), commands: vi.fn(), getDiagnostics: vi.fn(),
}));
vi.mock('../../plugins/offlineMode', () => ({ default: plugin }));
const offline = vi.hoisted(() => ({ value: true }));
vi.mock('../../hooks/useOfflineMode', () => ({ useOfflineMode: () => ({ offline: offline.value, android: true }) }));

import OfflineModePanel from './OfflineModePanel';
import { commandDeliveryLine } from '../../lib/offlineAnswers';

const at = new Date(2026, 10, 24, 15, 41).getTime();

describe('OfflineModePanel', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    platform.name = 'android';
    offline.value = true;
    plugin.cache.mockResolvedValue({ items: [
      { key: 'locations', version: at, data: { locations: [{ username: 'gracie', display_name: 'Gracie', zone: 'School' }] } },
      { key: 'shopping', version: at, data: { text: '- [ ] Milk\n- [x] Eggs' } },
    ] });
    plugin.requests.mockResolvedValue({ requests: [
      { id: '1', kind: 'shopping_add', body: { item: 'Bread' }, issued_at: 1, expires_at: 2, status: 'waiting_for_online_phone' },
      { id: '2', kind: 'ask', body: { text: 'Is the garage shut?' }, issued_at: 1, expires_at: 2, status: 'no_family_phone_nearby' },
      { id: '3', kind: 'ask', body: { text: 'Lights?' }, issued_at: 1, expires_at: 2, status: 'answered', answer: 'They are off.' },
    ], online: false, peerNearby: false });
    plugin.commands.mockResolvedValue({ commands: [] });
    plugin.queue.mockResolvedValue({ id: 'x' });
    plugin.sendCommand.mockResolvedValue({ id: 'y' });
  });

  it('says plainly that no family phone is nearby, and keeps every request with where it is', async () => {
    render(<OfflineModePanel />);
    expect(await screen.findByText('Is the garage shut?')).toBeInTheDocument();
    expect(screen.getByTestId('offline-mode-status')).toHaveTextContent('no family phone is nearby yet');
    expect(screen.getByText('No family phone nearby yet; kept until one is')).toBeInTheDocument();
    expect(screen.getByText('They are off.')).toBeInTheDocument();
  });

  it('answers where someone is from the saved locations, with their age, and queues the rest', async () => {
    render(<OfflineModePanel />);
    await screen.findByTestId('offline-locations');
    await userEvent.type(screen.getByLabelText('Question or command for Jarvis'), 'Where is Gracie?');
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }));
    expect(screen.getByTestId('offline-local-answer')).toHaveTextContent('Gracie was at School (as of 3:41 PM');
    expect(plugin.queue).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText('Question or command for Jarvis'), 'Turn off the porch light');
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }));
    expect(plugin.queue).toHaveBeenCalledWith({ kind: 'command', body: { text: 'Turn off the porch light' } });
  });

  it('shows queued shopping edits as not confirmed, and queues a tick as an edit', async () => {
    render(<OfflineModePanel />);
    const list = await screen.findByTestId('offline-shopping');
    expect(within(list).getByRole('button', { name: /Bread/ })).toHaveTextContent('Not confirmed yet');
    await userEvent.click(within(list).getByRole('button', { name: /Milk/ }));
    expect(plugin.queue).toHaveBeenCalledWith({ kind: 'shopping_check', body: { item: 'Milk' } });
  });

  it('lets a parent lock a child nearby and says whether it reached the phone', async () => {
    plugin.commands.mockResolvedValue({ commands: [
      { id: 'c', child: 'sam', action: 'lock', arg: 0, issued_at: at / 1000, reported: false },
    ] });
    render(<OfflineModePanel kids={[{ username: 'sam', name: 'Sam' }]} />);
    const kid = await screen.findByTestId('offline-child-sam');
    expect(await within(kid).findByTestId('offline-delivery')).toHaveTextContent('Locked sent at 3:41 PM. Not reached yet');
    await userEvent.click(within(kid).getByRole('button', { name: /Lock/ }));
    expect(plugin.sendCommand).toHaveBeenCalledWith({ child: 'sam', action: 'lock', minutes: 0 });
  });

  it('words a confirmed or refused command', () => {
    const base = { id: 'c', child: 'sam', arg: 15, issued_at: at / 1000, reported: true } as const;
    expect(commandDeliveryLine({ ...base, action: 'lock', confirmed_at: at / 1000, result: 'applied' }, 'Sam'))
      .toBe("Locked at 3:41 PM, confirmed by Sam's phone");
    expect(commandDeliveryLine({ ...base, action: 'bonus', confirmed_at: at / 1000, result: 'applied' }, 'Sam'))
      .toBe("15 more minutes at 3:41 PM, confirmed by Sam's phone");
    expect(commandDeliveryLine({ ...base, action: 'unlock', confirmed_at: at / 1000, result: 'refused_out_of_order' }, 'Sam'))
      .toBe("Sam's phone refused it (out of order)");
  });

  it('is not drawn in the browser', () => {
    platform.name = 'web';
    const { container } = render(<OfflineModePanel />);
    expect(container).toBeEmptyDOMElement();
  });
});
