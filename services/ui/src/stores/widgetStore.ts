import { create } from 'zustand';
import type {
  WidgetKey,
  WidgetSize,
  DeviceSortMode,
  WidgetDef,
  UserWidgetSettings,
  CapabilityPayload,
  WidgetInstance,
} from '../types/widget';
import { api } from '../services/api';

const defaultSizes: Record<WidgetKey, WidgetSize> = {
  energy_insights: 'medium',
  ambient_timer: 'small',
  quick_notes: 'medium',
  active_media: 'wide',
  chores_progress: 'tall',
  upcoming_events: 'wide',
  quick_assistant: 'medium',
  device_control: 'tall',
};

function createDefaultSettings(key: WidgetKey, order: number): UserWidgetSettings {
  return {
    widget_key: key,
    visibility: key === 'quick_assistant' ? 'hidden' : 'visible',
    order_index: order,
    size: defaultSizes[key],
    is_pinned: false,
    sort_mode: key === 'device_control' ? 'most_used' : null,
    pinned_devices: [],
    config: {},
    updated_at: Date.now(),
  };
}

const defaultWidgetDefs: WidgetDef[] = [
  { key: 'energy_insights', label: 'Energy Insights', icon: () => null, minSize: 'small', defaultSize: 'medium' },
  { key: 'ambient_timer', label: 'Ambient Timer', icon: () => null, minSize: 'small', defaultSize: 'small' },
  { key: 'quick_notes', label: 'Quick Notes', icon: () => null, minSize: 'small', defaultSize: 'medium' },
  { key: 'active_media', label: 'Active Media', icon: () => null, minSize: 'medium', defaultSize: 'wide' },
  { key: 'chores_progress', label: 'Chores Progress', icon: () => null, minSize: 'small', defaultSize: 'tall' },
  { key: 'upcoming_events', label: 'Upcoming Events', icon: () => null, minSize: 'small', defaultSize: 'wide' },
  { key: 'quick_assistant', label: 'Quick Assistant', icon: () => null, minSize: 'small', defaultSize: 'medium', requiresQuickAssistantEnabled: true },
  { key: 'device_control', label: 'Device Control', icon: () => null, minSize: 'small', defaultSize: 'tall' },
];

interface WidgetState {
  widgetRegistry: WidgetDef[];
  userWidgets: Record<string, UserWidgetSettings>;
  quickAssistantEnabled: boolean;
  mounting: boolean;
  error: string | null;
  mountCapabilities: CapabilityPayload;

  evaluateMountConditions: (capabilities: CapabilityPayload) => void;
  togglePin: (widgetKey: WidgetKey) => Promise<void>;
  updateOrder: (widgetKey: WidgetKey, newIndex: number) => Promise<void>;
  updateSize: (widgetKey: WidgetKey, newSize: WidgetSize) => Promise<void>;
  hideWidget: (widgetKey: WidgetKey) => Promise<void>;
  showWidget: (widgetKey: WidgetKey) => Promise<void>;
  removeWidget: (widgetKey: WidgetKey) => Promise<void>;
  setSortingMode: (widgetKey: WidgetKey, mode: DeviceSortMode) => Promise<void>;
  setQuickAssistantEnabled: (enabled: boolean) => Promise<void>;
  syncWithServer: () => Promise<void>;
  getActiveWidgets: (capabilities: CapabilityPayload) => WidgetInstance[];
  getVisibleWidgets: () => WidgetInstance[];
}

const defaultCapabilities: CapabilityPayload = {
  has_energy_data: false,
  has_active_media: false,
  has_chore_system: false,
  has_skylight: false,
  has_lights: false,
  has_tvs: false,
  has_timer: false,
  has_notes: false,
  has_events: false,
  has_quick_assistant: false,
  has_assignable_devices: false,
};

export const useWidgetStore = create<WidgetState>((set, get) => ({
  widgetRegistry: defaultWidgetDefs,
  userWidgets: {},
  quickAssistantEnabled: false,
  mounting: false,
  error: null,
  mountCapabilities: defaultCapabilities,

  syncWithServer: async () => {
    set({ mounting: true, error: null });
    try {
      const response = await api.getWidgetSettings() as { widgets: UserWidgetSettings[]; quick_assistant_enabled: boolean };
      const widgetsMap: Record<string, UserWidgetSettings> = {};
      for (const w of response.widgets) {
        widgetsMap[w.widget_key] = w;
      }
      set({ userWidgets: widgetsMap, quickAssistantEnabled: response.quick_assistant_enabled || false });
    } catch (e) {
      const error = e instanceof Error ? e.message : 'Failed to sync widget settings';
      set({ error, userWidgets: {} });
    } finally {
      set({ mounting: false });
    }
  },

  evaluateMountConditions: (capabilities: CapabilityPayload) => {
    set({ mountCapabilities: capabilities });
  },

  togglePin: async (widgetKey: WidgetKey) => {
    const current = get().userWidgets[widgetKey];
    if (!current) return;
    const updated = { ...current, is_pinned: !current.is_pinned, updated_at: Date.now() };
    set({ userWidgets: { ...get().userWidgets, [widgetKey]: updated } });
    try {
      await api.updateWidgetSettings(widgetKey, { is_pinned: !current.is_pinned });
    } catch {
      set({ userWidgets: { ...get().userWidgets, [widgetKey]: current } });
    }
  },

  updateOrder: async (widgetKey: WidgetKey, newIndex: number) => {
    const current = get().userWidgets[widgetKey];
    if (!current) return;
    const updated = { ...current, order_index: newIndex, updated_at: Date.now() };
    set({ userWidgets: { ...get().userWidgets, [widgetKey]: updated } });
    try {
      await api.updateWidgetSettings(widgetKey, { order_index: newIndex });
    } catch {
      set({ userWidgets: { ...get().userWidgets, [widgetKey]: current } });
    }
  },

  updateSize: async (widgetKey: WidgetKey, newSize: WidgetSize) => {
    const current = get().userWidgets[widgetKey];
    if (!current) return;
    const updated = { ...current, size: newSize, updated_at: Date.now() };
    set({ userWidgets: { ...get().userWidgets, [widgetKey]: updated } });
    try {
      await api.updateWidgetSettings(widgetKey, { size: newSize });
    } catch {
      set({ userWidgets: { ...get().userWidgets, [widgetKey]: current } });
    }
  },

  hideWidget: async (widgetKey: WidgetKey) => {
    const current = get().userWidgets[widgetKey];
    if (!current) return;
    const updated = { ...current, visibility: 'hidden' as const, is_pinned: false, updated_at: Date.now() };
    set({ userWidgets: { ...get().userWidgets, [widgetKey]: updated } });
    try {
      await api.updateWidgetSettings(widgetKey, { visibility: 'hidden' });
    } catch {
      set({ userWidgets: { ...get().userWidgets, [widgetKey]: current } });
    }
  },

  showWidget: async (widgetKey: WidgetKey) => {
    const current = get().userWidgets[widgetKey];
    if (!current) return;
    const updated = { ...current, visibility: 'visible' as const, updated_at: Date.now() };
    set({ userWidgets: { ...get().userWidgets, [widgetKey]: updated } });
    try {
      await api.updateWidgetSettings(widgetKey, { visibility: 'visible' });
    } catch {
      set({ userWidgets: { ...get().userWidgets, [widgetKey]: current } });
    }
  },

  removeWidget: async (widgetKey: WidgetKey) => {
    const current = get().userWidgets[widgetKey];
    if (!current) return;
    const updated = { ...current, visibility: 'removed' as const, updated_at: Date.now() };
    set({ userWidgets: { ...get().userWidgets, [widgetKey]: updated } });
    try {
      await api.updateWidgetSettings(widgetKey, { visibility: 'removed' });
    } catch {
      set({ userWidgets: { ...get().userWidgets, [widgetKey]: current } });
    }
  },

  setSortingMode: async (widgetKey: WidgetKey, mode: DeviceSortMode) => {
    const current = get().userWidgets[widgetKey];
    if (!current) return;
    const updated = { ...current, sort_mode: mode, updated_at: Date.now() };
    set({ userWidgets: { ...get().userWidgets, [widgetKey]: updated } });
    try {
      await api.updateWidgetSettings(widgetKey, { sort_mode: mode });
    } catch {
      set({ userWidgets: { ...get().userWidgets, [widgetKey]: current } });
    }
  },

  setQuickAssistantEnabled: async (enabled: boolean) => {
    set({ quickAssistantEnabled: enabled });
    try {
      await api.updateWidgetSettings('quick_assistant', { quick_assistant_enabled: enabled });
    } catch {
      set({ quickAssistantEnabled: !enabled });
    }
  },

  getActiveWidgets: (capabilities: CapabilityPayload) => {
    const { userWidgets, quickAssistantEnabled, widgetRegistry } = get();
    return widgetRegistry
      .filter((def) => {
        if (def.mountConditions && !def.mountConditions(capabilities)) return false;
        const settings = userWidgets[def.key];
        if (settings) {
          if (settings.visibility === 'removed' || settings.visibility === 'hidden') return false;
        } else {
          const defaultSettings = createDefaultSettings(def.key, 0);
          if (defaultSettings.visibility === 'hidden' || defaultSettings.visibility === 'removed') return false;
        }
        if (def.requiresQuickAssistantEnabled && !quickAssistantEnabled) return false;
        return true;
      })
      .map((def, index) => ({
        def,
        userSettings: userWidgets[def.key] ?? createDefaultSettings(def.key, index),
        isActive: true,
      }))
      .sort((a, b) => a.userSettings.order_index - b.userSettings.order_index);
  },

  getVisibleWidgets: () => {
    return get().getActiveWidgets({
      has_energy_data: false,
      has_active_media: false,
      has_chore_system: false,
      has_skylight: false,
      has_lights: false,
      has_tvs: false,
      has_timer: false,
      has_notes: false,
      has_events: false,
      has_quick_assistant: false,
      has_assignable_devices: false,
    });
  },
}));
