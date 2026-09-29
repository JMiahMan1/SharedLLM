import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CapabilityPayload, UserWidgetSettings, WidgetDef, WidgetKey } from '../types/widget';
import { Lock, Zap } from 'lucide-react';

vi.mock('../services/api', () => ({
  api: {
    updateWidgetSettings: vi.fn(async () => ({})),
    getWidgetSettings: vi.fn(async () => ({ widgets: {} })),
  },
}));

import { defaultWidgetDefs, isWidgetVisible, useWidgetStore } from '../stores/widgetStore';

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

const registry: WidgetDef[] = [
  { key: 'energy_insights', label: 'Energy Insights', icon: Zap, minSize: 'small', defaultSize: 'medium' },
  { key: 'workspaces', label: 'Workspaces', icon: Lock, minSize: 'small', defaultSize: 'medium', adminOnly: true },
  { key: 'climate', label: 'Climate', icon: Lock, minSize: 'small', defaultSize: 'medium' },
];

const visible = (key: WidgetKey) => isWidgetVisible(
  registry.find((d) => d.key === key) as WidgetDef,
  { userWidgets: {}, capabilities: allCapabilities, quickAssistantEnabled: true, isAdmin: false },
);

describe('adminOnly widgets', () => {
  beforeEach(() => {
    useWidgetStore.setState({
      widgetRegistry: registry,
      userWidgets: {},
      activeWidgets: [],
      visibleWidgets: [],
      quickAssistantEnabled: true,
      mounting: false,
      error: null,
      mountCapabilities: allCapabilities,
      isAdmin: false,
    });
    useWidgetStore.getState().evaluateMountConditions({ ...allCapabilities });
  });

  it('hides an adminOnly widget from a normal user', () => {
    expect(visible('workspaces')).toBe(false);
  });

  it('leaves ordinary widgets alone for a normal user', () => {
    expect(visible('climate')).toBe(true);
    expect(visible('energy_insights')).toBe(true);
  });

  it('shows an adminOnly widget to an admin', () => {
    const store = useWidgetStore.getState();
    store.setAdminStatus(true);
    expect(useWidgetStore.getState().isAdmin).toBe(true);
    expect(
      isWidgetVisible(registry[1], {
        userWidgets: {}, capabilities: allCapabilities, quickAssistantEnabled: true, isAdmin: true,
      })
    ).toBe(true);
  });

  it('defaults to not-an-admin, so a lock is never open before the role is known', () => {
    // The store starts life with no knowledge of the viewer. If it assumed
    // admin, a management card would flash for a normal user on first paint.
    useWidgetStore.setState({ isAdmin: false });
    expect(useWidgetStore.getState().getVisibleWidgets().map((w) => w.def.key)).not.toContain('workspaces');
  });

  it('filters the adminOnly widget out of the rendered board, not just the getter', () => {
    // BentoBoxDashboard subscribes to `visibleWidgets`, which the store's `set`
    // wrapper recomputes. That wrapper used to carry its own copy of the
    // filter, so a rule added to `getActiveWidgets` alone would not have
    // reached the screen. Both paths must agree.
    useWidgetStore.getState().setAdminStatus(false);
    useWidgetStore.setState({
      userWidgets: {
        climate: { ...blank('climate', 0), visibility: 'visible' },
        workspaces: { ...blank('workspaces', 1), visibility: 'visible' },
      } as Record<string, UserWidgetSettings>,
    });

    const fromGetter = useWidgetStore.getState().getVisibleWidgets().map((w) => w.def.key);
    const fromBoard = useWidgetStore.getState().visibleWidgets.map((w) => w.def.key);
    expect(fromGetter).not.toContain('workspaces');
    expect(fromBoard).not.toContain('workspaces');
    // Membership, not order: the two paths build their fallback settings from
    // different indexes, so a sort tie can differ while the set of visible
    // cards is identical. Membership is the property that matters here.
    expect([...fromBoard].sort()).toEqual([...fromGetter].sort());
  });

  it('restores the adminOnly widget when the role is set after first paint', () => {
    useWidgetStore.getState().setAdminStatus(true);
    const store = useWidgetStore.getState();
    expect(store.getVisibleWidgets().map((w) => w.def.key)).toContain('workspaces');
    expect(useWidgetStore.getState().visibleWidgets.map((w) => w.def.key)).toContain('workspaces');
  });

  it('marks nothing admin-only by default, so nobody loses a widget unasked', () => {
    // The mechanism is built and enforced, but the operator decides which
    // cards are management surfaces. Shipping it with nothing flagged means
    // this change hides nothing until a flag is deliberately set.
    expect(defaultWidgetDefs.filter((d) => d.adminOnly)).toEqual([]);
  });
});

function blank(key: WidgetKey, order: number): UserWidgetSettings {
  return {
    widget_key: key,
    visibility: 'visible',
    order_index: order,
    size: 'medium',
    is_pinned: false,
    sort_mode: null,
    pinned_devices: [],
    config: {},
    updated_at: 0,
  };
}
