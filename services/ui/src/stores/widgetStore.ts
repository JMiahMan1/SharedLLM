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

export interface WidgetStateItem {
  id: string;
  type: string;
  isVisible: boolean;
  size: WidgetSize | 'normal' | 'large';
  config: Record<string, unknown>;
}

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

export const defaultWidgetDefs: WidgetDef[] = [
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
  activeWidgets: WidgetStateItem[];
  quickAssistantEnabled: boolean;
  mounting: boolean;
  error: string | null;
  mountCapabilities: CapabilityPayload;
  visibleWidgets: WidgetInstance[];

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
  updateWidgetConfig: (id: string, config: Record<string, unknown>) => Promise<void>;
  togglePinnedDevice: (widgetKey: WidgetKey, deviceId: string) => Promise<void>;
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

let activeSyncPromise: Promise<void> | null = null;
let lastSyncTime = 0;
const SYNC_COOLDOWN_MS = 5000;

export const useWidgetStore = create<WidgetState>((rawSet, get) => {
  const set = (
    partial: WidgetState | Partial<WidgetState> | ((state: WidgetState) => WidgetState | Partial<WidgetState>),
    replace?: boolean
  ) => {
    (rawSet as (
      p: WidgetState | Partial<WidgetState> | ((state: WidgetState) => WidgetState | Partial<WidgetState>),
      r?: boolean
    ) => void)(
      (state) => {
        const next = typeof partial === 'function' ? partial(state) : partial;
        const merged = { ...state, ...next };
      const visibleWidgets = merged.widgetRegistry
        .filter((def) => {
          if (def.mountConditions && !def.mountConditions(merged.mountCapabilities)) return false;
          const settings = merged.userWidgets[def.key];
          if (settings) {
            if (settings.visibility === 'removed' || settings.visibility === 'hidden') return false;
          } else {
            const defaultSettings = createDefaultSettings(def.key, 0);
            if (defaultSettings.visibility === 'hidden' || defaultSettings.visibility === 'removed') return false;
          }
          if (def.requiresQuickAssistantEnabled && !merged.quickAssistantEnabled) return false;
          return true;
        })
        .map((def, index) => ({
          def,
          userSettings: merged.userWidgets[def.key] ?? createDefaultSettings(def.key, index),
          isActive: true,
        }))
        .sort((a, b) => a.userSettings.order_index - b.userSettings.order_index);

      return { ...next, visibleWidgets };
    }, replace);
  };

  return {
    widgetRegistry: defaultWidgetDefs,
    userWidgets: {},
    activeWidgets: [],
    quickAssistantEnabled: false,
    mounting: false,
    error: null,
    mountCapabilities: defaultCapabilities,
    visibleWidgets: [],

  syncWithServer: async () => {
    if (activeSyncPromise) {
      return activeSyncPromise;
    }

    const now = Date.now();
    if (now - lastSyncTime < SYNC_COOLDOWN_MS && Object.keys(get().userWidgets).length > 0) {
      return;
    }

    activeSyncPromise = (async () => {
      set({ mounting: true, error: null });
      try {
        const response = await api.getWidgetSettings() as { widgets: UserWidgetSettings[]; quick_assistant_enabled: boolean };
        const widgetsMap: Record<string, UserWidgetSettings> = {};
        for (const w of response.widgets) {
          widgetsMap[w.widget_key] = w;
        }
        
        const activeWidgets: WidgetStateItem[] = defaultWidgetDefs.map((def, index) => {
          const w = widgetsMap[def.key] || createDefaultSettings(def.key, index);
          return {
            id: def.key,
            type: def.key,
            isVisible: w.visibility === 'visible',
            size: w.size,
            config: w.config || {},
          };
        });

        set({
          userWidgets: widgetsMap,
          activeWidgets,
          quickAssistantEnabled: response.quick_assistant_enabled || false,
        });
        lastSyncTime = Date.now();
      } catch (e) {
        const error = e instanceof Error ? e.message : 'Failed to sync widget settings';
        // On failure: surface error but keep default widgets visible so the
        // dashboard never shows a blank screen.
        const fallbackWidgets: Record<string, UserWidgetSettings> = {};
        const fallbackActive: WidgetStateItem[] = defaultWidgetDefs.map((def, index) => {
          const settings = createDefaultSettings(def.key, index);
          fallbackWidgets[def.key] = settings;
          return {
            id: def.key,
            type: def.key,
            isVisible: settings.visibility === 'visible',
            size: settings.size,
            config: {},
          };
        });
        // Only populate if we don't already have synced state
        if (Object.keys(get().userWidgets).length === 0) {
          set({ error, userWidgets: fallbackWidgets, activeWidgets: fallbackActive });
        } else {
          set({ error });
        }
      } finally {
        set({ mounting: false });
        activeSyncPromise = null;
      }
    })();

    return activeSyncPromise;
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
    const updatedActiveWidgets = get().activeWidgets.map((item) =>
      item.id === widgetKey ? { ...item, size: newSize } : item
    );
    set({
      userWidgets: { ...get().userWidgets, [widgetKey]: updated },
      activeWidgets: updatedActiveWidgets,
    });
    try {
      await api.updateWidgetSettings(widgetKey, { size: newSize });
    } catch {
      set({
        userWidgets: { ...get().userWidgets, [widgetKey]: current },
        activeWidgets: get().activeWidgets.map((item) =>
          item.id === widgetKey ? { ...item, size: current.size } : item
        ),
      });
    }
  },

  hideWidget: async (widgetKey: WidgetKey) => {
    const current = get().userWidgets[widgetKey];
    if (!current) return;
    const updated = { ...current, visibility: 'hidden' as const, is_pinned: false, updated_at: Date.now() };
    const updatedActiveWidgets = get().activeWidgets.map((item) =>
      item.id === widgetKey ? { ...item, isVisible: false } : item
    );
    set({
      userWidgets: { ...get().userWidgets, [widgetKey]: updated },
      activeWidgets: updatedActiveWidgets,
    });
    try {
      await api.updateWidgetSettings(widgetKey, { visibility: 'hidden' });
    } catch {
      set({
        userWidgets: { ...get().userWidgets, [widgetKey]: current },
        activeWidgets: get().activeWidgets.map((item) =>
          item.id === widgetKey ? { ...item, isVisible: current.visibility === 'visible' } : item
        ),
      });
    }
  },

  showWidget: async (widgetKey: WidgetKey) => {
    const current = get().userWidgets[widgetKey];
    if (!current) return;
    const updated = { ...current, visibility: 'visible' as const, updated_at: Date.now() };
    const updatedActiveWidgets = get().activeWidgets.map((item) =>
      item.id === widgetKey ? { ...item, isVisible: true } : item
    );
    set({
      userWidgets: { ...get().userWidgets, [widgetKey]: updated },
      activeWidgets: updatedActiveWidgets,
    });
    try {
      await api.updateWidgetSettings(widgetKey, { visibility: 'visible' });
    } catch {
      set({
        userWidgets: { ...get().userWidgets, [widgetKey]: current },
        activeWidgets: get().activeWidgets.map((item) =>
          item.id === widgetKey ? { ...item, isVisible: current.visibility === 'visible' } : item
        ),
      });
    }
  },

  removeWidget: async (widgetKey: WidgetKey) => {
    const current = get().userWidgets[widgetKey];
    if (!current) return;
    const updated = { ...current, visibility: 'removed' as const, updated_at: Date.now() };
    const updatedActiveWidgets = get().activeWidgets.map((item) =>
      item.id === widgetKey ? { ...item, isVisible: false } : item
    );
    set({
      userWidgets: { ...get().userWidgets, [widgetKey]: updated },
      activeWidgets: updatedActiveWidgets,
    });
    try {
      await api.updateWidgetSettings(widgetKey, { visibility: 'removed' });
    } catch {
      set({
        userWidgets: { ...get().userWidgets, [widgetKey]: current },
        activeWidgets: get().activeWidgets.map((item) =>
          item.id === widgetKey ? { ...item, isVisible: current.visibility === 'visible' } : item
        ),
      });
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
    // Use the capabilities that were last evaluated (e.g. from server sync)
    // rather than hard-coding all-false which would hide capability-gated widgets.
    return get().getActiveWidgets(get().mountCapabilities);
  },

  updateWidgetConfig: async (id: string, config: Record<string, unknown>) => {
    const current = get().userWidgets[id] || createDefaultSettings(id as never, 0);
    const updatedConfig = { ...(current.config || {}), ...config };
    const updatedWidget = { ...current, config: updatedConfig, updated_at: Date.now() };

    const updatedActiveWidgets = get().activeWidgets.map((item) =>
      item.id === id ? { ...item, config: updatedConfig } : item
    );

    set({
      userWidgets: { ...get().userWidgets, [id]: updatedWidget },
      activeWidgets: updatedActiveWidgets,
    });

    try {
      await api.updateWidgetSettings(id as never, { config: updatedConfig });
    } catch {
      set({
        userWidgets: { ...get().userWidgets, [id]: current },
        activeWidgets: get().activeWidgets.map((item) =>
          item.id === id ? { ...item, config: current.config || {} } : item
        ),
      });
    }
  },

  togglePinnedDevice: async (widgetKey: WidgetKey, deviceId: string) => {
    const current = get().userWidgets[widgetKey];
    if (!current) return;
    const pinned = current.pinned_devices || [];
    const updatedPinned = pinned.includes(deviceId)
      ? pinned.filter((id) => id !== deviceId)
      : [...pinned, deviceId];
    const updated = { ...current, pinned_devices: updatedPinned, updated_at: Date.now() };
    set({ userWidgets: { ...get().userWidgets, [widgetKey]: updated } });
    try {
      await api.updateWidgetSettings(widgetKey, { pinned_devices: updatedPinned });
    } catch {
      set({ userWidgets: { ...get().userWidgets, [widgetKey]: current } });
    }
  },
  };
});

export function useWidget(key: WidgetKey): UserWidgetSettings | undefined {
  return useWidgetStore((state) => state.userWidgets[key]);
}
