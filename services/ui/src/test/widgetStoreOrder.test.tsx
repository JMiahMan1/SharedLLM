import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CapabilityPayload, UserWidgetSettings, WidgetKey } from '../types/widget';

vi.mock('../services/api', () => ({
  api: {
    updateWidgetSettings: vi.fn(async () => ({})),
    getWidgetSettings: vi.fn(async () => ({ widgets: {} })),
  },
}));

import { api } from '../services/api';
import { defaultWidgetDefs, useWidgetStore } from '../stores/widgetStore';

const allCapabilities = {
  has_energy_data: true,
  has_active_media: true,
  has_chore_system: true,
  has_skylight: true,
  has_lights: true,
  has_tvs: true,
  has_timer: true,
  has_notes: true,
  has_events: true,
  has_quick_assistant: true,
  has_assignable_devices: true,
} satisfies CapabilityPayload;

function settingsFor(key: WidgetKey, orderIndex: number, isPinned = false): UserWidgetSettings {
  return {
    widget_key: key,
    visibility: 'visible',
    order_index: orderIndex,
    size: 'medium',
    is_pinned: isPinned,
    sort_mode: null,
    pinned_devices: [],
    config: {},
    updated_at: 0,
  };
}

/** Seed every registered widget with explicit settings and recompute the board. */
function seed(userWidgets: Partial<Record<WidgetKey, UserWidgetSettings>>) {
  useWidgetStore.setState((state) => ({
    userWidgets: { ...userWidgets } as Record<string, UserWidgetSettings>,
    quickAssistantEnabled: true,
    mountCapabilities: { ...state.mountCapabilities, ...allCapabilities },
  }));
  useWidgetStore.getState().evaluateMountConditions({ ...allCapabilities });
}

const keys = defaultWidgetDefs.map((def) => def.key);
const boardKeys = () => useWidgetStore.getState().visibleWidgets.map((w) => w.def.key);

describe('widgetStore ordering', () => {
  beforeEach(() => {
    vi.mocked(api.updateWidgetSettings).mockClear();
    useWidgetStore.setState({
      widgetRegistry: defaultWidgetDefs,
      userWidgets: {},
      quickAssistantEnabled: false,
      mounting: true,
    });
  });

  it('every default widget renders a real icon', () => {
    // A null icon renders nothing at all, which silently blanks the widget
    // header and the settings catalog.
    for (const def of defaultWidgetDefs) {
      const Icon = def.icon;
      const { container } = render(<Icon size={16} />);
      expect(container.querySelector('svg'), `${def.key} renders no icon`).not.toBeNull();
    }
  });

  it('reindexes the board densely and persists only the widgets that moved', async () => {
    const [a, b, c, d] = keys;
    seed({
      [a]: settingsFor(a, 0),
      [b]: settingsFor(b, 1),
      [c]: settingsFor(c, 2),
      [d]: settingsFor(d, 3),
    });
    expect(boardKeys().slice(0, 4)).toEqual([a, b, c, d]);

    await useWidgetStore.getState().updateOrder(a, 2);

    const widgets = useWidgetStore.getState().userWidgets;
    expect(widgets[b].order_index).toBe(0);
    expect(widgets[c].order_index).toBe(1);
    expect(widgets[a].order_index).toBe(2);
    expect(widgets[d].order_index).toBe(3);

    // d kept its index, so it must not be written back to the server.
    const persisted = vi.mocked(api.updateWidgetSettings).mock.calls.map(([key]) => key);
    expect(persisted).toContain(a);
    expect(persisted).toContain(b);
    expect(persisted).toContain(c);
    expect(persisted).not.toContain(d);
  });

  it('never lets repeated moves drift past the end of the board', async () => {
    seed(Object.fromEntries(keys.map((key, index) => [key, settingsFor(key, index)])));
    const first = boardKeys()[0];

    for (let move = 0; move < 3; move += 1) {
      await useWidgetStore.getState().updateOrder(first, keys.length);
    }

    const indices = useWidgetStore
      .getState()
      .visibleWidgets.map((w) => w.userSettings.order_index)
      .sort((x, y) => x - y);
    expect(indices).toEqual(keys.map((_, index) => index));
    expect(boardKeys()[keys.length - 1]).toBe(first);
  });

  it('keeps pinned widgets ahead of the user order', () => {
    const [a, b, c] = keys;
    seed({
      [a]: settingsFor(a, 0),
      [b]: settingsFor(b, 1),
      [c]: settingsFor(c, 2, true),
    });

    expect(boardKeys()[0]).toBe(c);
    expect(boardKeys().slice(1, 3)).toEqual([a, b]);
  });

  it('orders deterministically when widgets share an order_index', () => {
    const [a, b] = keys;
    seed({
      [a]: settingsFor(a, 0),
      [b]: settingsFor(b, 0),
    });

    const first = boardKeys().slice(0, 2);
    useWidgetStore.getState().evaluateMountConditions({ ...allCapabilities });
    expect(boardKeys().slice(0, 2)).toEqual(first);
    expect(new Set(first).size).toBe(2);
  });
});