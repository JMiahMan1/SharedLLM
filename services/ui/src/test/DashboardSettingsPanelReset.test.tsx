import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../services/api', () => ({
  api: { updateWidgetSettings: vi.fn(async () => ({ status: 'SUCCESS' })) },
}));
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

import toast from 'react-hot-toast';
import { api } from '../services/api';
import DashboardSettingsPanel from '../components/dashboard/DashboardSettingsPanel';
import { defaultWidgetDefs, useWidgetStore } from '../stores/widgetStore';
import type { UserWidgetSettings } from '../types/widget';

function defaults(): Record<string, UserWidgetSettings> {
  return Object.fromEntries(defaultWidgetDefs.map((def, i) => [def.key, {
    widget_key: def.key,
    visibility: def.key === 'quick_assistant' ? 'hidden' : 'visible',
    order_index: i,
    size: def.defaultSize,
    is_pinned: false,
    sort_mode: null,
    pinned_devices: [],
    config: {},
    updated_at: 0,
  } as UserWidgetSettings]));
}

const resetAll = async () => {
  fireEvent.click(screen.getByRole('button', { name: /Reset All/ }));
  fireEvent.click(screen.getByRole('button', { name: /Yes, reset all/ }));
};

describe('DashboardSettingsPanel reset', () => {
  beforeEach(() => {
    vi.mocked(api.updateWidgetSettings).mockClear();
    vi.mocked(toast).mockClear();
  });

  it('resets a reordered board and keeps favourite devices and widget config', async () => {
    const widgets = defaults();
    widgets.device_control = { ...widgets.device_control, order_index: 0, pinned_devices: ['light.kitchen'] };
    widgets.climate = { ...widgets.climate, config: { devices: ['climate.hall'] } };
    useWidgetStore.setState({ userWidgets: widgets, quickAssistantEnabled: false });

    render(<DashboardSettingsPanel isOpen onClose={() => {}} />);
    await resetAll();

    await waitFor(() => expect(api.updateWidgetSettings).toHaveBeenCalled());
    const state = useWidgetStore.getState().userWidgets;
    expect(state.device_control.order_index).toBe(defaultWidgetDefs.findIndex((d) => d.key === 'device_control'));
    expect(state.device_control.pinned_devices).toEqual(['light.kitchen']);
    expect(state.climate.config).toEqual({ devices: ['climate.hall'] });
  });

  it('says so when the board is already at the defaults', async () => {
    useWidgetStore.setState({ userWidgets: defaults(), quickAssistantEnabled: false });
    render(<DashboardSettingsPanel isOpen onClose={() => {}} />);
    await resetAll();
    expect(toast).toHaveBeenCalledWith('Already using the default layout');
    expect(api.updateWidgetSettings).not.toHaveBeenCalled();
    // The confirmation closes either way.
    expect(screen.queryByRole('button', { name: /Yes, reset all/ })).toBeNull();
  });
});
