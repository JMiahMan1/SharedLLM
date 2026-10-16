import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api', () => ({
  api: {
    updateWidgetSettings: vi.fn(async () => ({ status: 'SUCCESS' })),
    getWidgetSettings: vi.fn(async () => ({ widgets: [], quick_assistant_enabled: false })),
  },
}));

import { api } from '../services/api';
import { defaultWidgetDefs, useWidgetStore } from '../stores/widgetStore';

const onBoard = () => useWidgetStore.getState().visibleWidgets.some((w) => w.def.key === 'quick_assistant');

describe('widgetStore quick assistant', () => {
  beforeEach(() => {
    vi.mocked(api.updateWidgetSettings).mockClear();
    useWidgetStore.setState({ widgetRegistry: defaultWidgetDefs, userWidgets: {}, quickAssistantEnabled: false });
  });

  it('"Show" puts the assistant on the board straight away and enables it on the server', async () => {
    expect(onBoard()).toBe(false);
    await useWidgetStore.getState().showWidget('quick_assistant');
    expect(onBoard()).toBe(true);
    expect(api.updateWidgetSettings).toHaveBeenCalledWith('quick_assistant', { quick_assistant_enabled: true });
  });

  it('"Hide" disables it again', async () => {
    await useWidgetStore.getState().showWidget('quick_assistant');
    await useWidgetStore.getState().hideWidget('quick_assistant');
    expect(onBoard()).toBe(false);
    expect(useWidgetStore.getState().quickAssistantEnabled).toBe(false);
  });

  it('rolls back when the server refuses', async () => {
    vi.mocked(api.updateWidgetSettings).mockRejectedValueOnce(new Error('down'));
    await useWidgetStore.getState().showWidget('quick_assistant');
    expect(onBoard()).toBe(false);
  });
});
