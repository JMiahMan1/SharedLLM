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
import {
  Zap,
  Timer,
  StickyNote,
  Music,
  ListChecks,
  CalendarDays,
  Sparkles,
  LayoutGrid,
  LayoutDashboard,
  Activity,
  Thermometer,
} from 'lucide-react';

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
  workspaces: 'medium',
  health_activity: 'medium',
  climate: 'medium',
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
  { key: 'energy_insights', label: 'Energy Insights', icon: Zap, minSize: 'small', defaultSize: 'medium' },
  { key: 'ambient_timer', label: 'Ambient Timer', icon: Timer, minSize: 'small', defaultSize: 'small' },
  { key: 'quick_notes', label: 'Quick Notes', icon: StickyNote, minSize: 'small', defaultSize: 'medium' },
  { key: 'active_media', label: 'Active Media', icon: Music, minSize: 'medium', defaultSize: 'wide' },
  { key: 'chores_progress', label: 'Chores Progress', icon: ListChecks, minSize: 'small', defaultSize: 'tall' },
  { key: 'upcoming_events', label: 'Upcoming Events', icon: CalendarDays, minSize: 'small', defaultSize: 'wide' },
  { key: 'quick_assistant', label: 'Quick Assistant', icon: Sparkles, minSize: 'small', defaultSize: 'medium', requiresQuickAssistantEnabled: true },
  { key: 'device_control', label: 'Device Control', icon: LayoutGrid, minSize: 'small', defaultSize: 'tall' },
  { key: 'workspaces', label: 'Workspaces', icon: LayoutDashboard, minSize: 'small', defaultSize: 'medium' },
  { key: 'health_activity', label: 'Health', icon: Activity, minSize: 'small', defaultSize: 'medium' },
  { key: 'climate', label: 'Climate', icon: Thermometer, minSize: 'small', defaultSize: 'medium' },
];

/**
 * The single visibility rule for a widget, shared by every call site.
 *
 * This lives as one function on purpose. The `set` wrapper below and
 * `getActiveWidgets` used to carry two hand-rolled copies of the same filter,
 * which meant a rule added to one was silently missing from the other — and
 * `BentoBoxDashboard` subscribes to `visibleWidgets`, the copy the `set`
 * wrapper produces. Filtering in one place and not the other is a bypass, so
 * there is now nowhere for a rule to be forgotten.
 */
export function isWidgetVisible(
  def: WidgetDef,
  ctx: {
    userWidgets: Record<string, UserWidgetSettings>;
    capabilities: CapabilityPayload;
    quickAssistantEnabled: boolean;
    isAdmin: boolean;
  },
): boolean {
  // Management widgets are hidden from normal users before anything else is
  // considered, so their existence is not even hinted at by the registry.
  if (def.adminOnly && !ctx.isAdmin) return false;
  if (def.mountConditions && !def.mountConditions(ctx.capabilities)) return false;
  const settings = ctx.userWidgets[def.key];
  if (settings) {
    if (settings.visibility === 'removed' || settings.visibility === 'hidden') return false;
  } else {
    const defaultSettings = createDefaultSettings(def.key, 0);
    if (defaultSettings.visibility === 'hidden' || defaultSettings.visibility === 'removed') return false;
  }
  if (def.requiresQuickAssistantEnabled && !ctx.quickAssistantEnabled) return false;
  return true;
}

/**
 * Widgets display pinned-first, then in user order. The key tiebreaker keeps
 * the sequence stable when two widgets share an order_index — legacy or
 * imported rows defaulting to 0 would otherwise shuffle between renders.
 */
function compareWidgetInstances(a: WidgetInstance, b: WidgetInstance): number {
  if (a.userSettings.is_pinned !== b.userSettings.is_pinned) {
    return a.userSettings.is_pinned ? -1 : 1;
  }
  if (a.userSettings.order_index !== b.userSettings.order_index) {
    return a.userSettings.order_index - b.userSettings.order_index;
  }
  return a.def.key.localeCompare(b.def.key);
}

interface WidgetState {
  widgetRegistry: WidgetDef[];
  userWidgets: Record<string, UserWidgetSettings>;
  activeWidgets: WidgetStateItem[];
  quickAssistantEnabled: boolean;
  mounting: boolean;
  error: string | null;
  mountCapabilities: CapabilityPayload;
  visibleWidgets: WidgetInstance[];
  /** Whether the signed-in user may see `adminOnly` widgets. Defaults to false. */
  isAdmin: boolean;

  setAdminStatus: (isAdmin: boolean) => void;
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
  replaceAllWidgets: (widgets: Record<string, UserWidgetSettings>) => void;
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
        .filter((def) => isWidgetVisible(def, {
          userWidgets: merged.userWidgets,
          capabilities: merged.mountCapabilities,
          quickAssistantEnabled: merged.quickAssistantEnabled,
          isAdmin: merged.isAdmin,
        }))
        .map((def, index) => ({
          def,
          userSettings: merged.userWidgets[def.key] ?? createDefaultSettings(def.key, index),
          isActive: true,
        }))
        .sort(compareWidgetInstances);

      return { ...next, visibleWidgets };
    }, replace);
  };

  return {
    widgetRegistry: defaultWidgetDefs,
    userWidgets: {},
    activeWidgets: [],
    quickAssistantEnabled: false,
    mounting: true,
    error: null,
    mountCapabilities: defaultCapabilities,
    visibleWidgets: [],
    // Fail closed: until something authoritative says otherwise, the viewer is
    // not an admin, so `adminOnly` widgets stay hidden. The one-frame delay
    // for an admin is the safe direction to be wrong in.
    isAdmin: false,

    setAdminStatus: (isAdmin: boolean) => {
      if (get().isAdmin === isAdmin) return;
      set({ isAdmin });
    },

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
        const serverWidgets = Array.isArray(response.widgets) ? response.widgets : [];
        for (const w of serverWidgets) {
          if (w && typeof w.widget_key === 'string') {
            widgetsMap[w.widget_key] = w;
          }
        }
        // Fill any registry keys the server omitted (e.g. pre-migration data).
        for (const [index, def] of defaultWidgetDefs.entries()) {
          if (!widgetsMap[def.key]) {
            widgetsMap[def.key] = createDefaultSettings(def.key, index);
          }
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

  replaceAllWidgets: (widgets: Record<string, UserWidgetSettings>) => {
    set({ userWidgets: widgets });
  },

  togglePin: async (widgetKey: WidgetKey) => {
    const registryIndex = get().widgetRegistry.findIndex((d) => d.key === widgetKey);
    const current = get().userWidgets[widgetKey] ?? createDefaultSettings(widgetKey, Math.max(0, registryIndex));
    const updated = { ...current, is_pinned: !current.is_pinned, updated_at: Date.now() };
    set({ userWidgets: { ...get().userWidgets, [widgetKey]: updated } });
    try {
      await api.updateWidgetSettings(widgetKey, { is_pinned: !current.is_pinned });
    } catch {
      set({ userWidgets: { ...get().userWidgets, [widgetKey]: current } });
    }
  },

  updateOrder: async (widgetKey: WidgetKey, newIndex: number) => {
    const previousWidgets = get().userWidgets;
    // Resequence the whole board: pull the widget out of the order the user is
    // looking at, re-insert it at the requested slot, then hand every widget a
    // dense 0..n-1 index. Assigning a single raw index instead lets repeated
    // moves drift past the end of the list and collide with one another.
    const ordered = [...get().visibleWidgets];
    const from = ordered.findIndex((w) => w.def.key === widgetKey);
    const [moved] = from === -1 ? [] : ordered.splice(from, 1);
    if (!moved) return;

    const target = Math.max(0, Math.min(newIndex, ordered.length));
    ordered.splice(target, 0, moved);

    const now = Date.now();
    const nextWidgets: Record<string, UserWidgetSettings> = { ...previousWidgets };
    const reordered = ordered.map((instance, index) => {
      const key = instance.def.key;
      nextWidgets[key] = { ...instance.userSettings, order_index: index, updated_at: now };
      return { key, index, changed: previousWidgets[key]?.order_index !== index };
    });

    set({ userWidgets: nextWidgets });

    try {
      await Promise.all(
        reordered
          .filter((w) => w.changed)
          .map((w) => api.updateWidgetSettings(w.key, { order_index: w.index }))
      );
    } catch {
      set({ userWidgets: previousWidgets });
    }
  },

  updateSize: async (widgetKey: WidgetKey, newSize: WidgetSize) => {
    const registryIndex = get().widgetRegistry.findIndex((d) => d.key === widgetKey);
    const current = get().userWidgets[widgetKey] ?? createDefaultSettings(widgetKey, Math.max(0, registryIndex));
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
    const registryIndex = get().widgetRegistry.findIndex((d) => d.key === widgetKey);
    const current = get().userWidgets[widgetKey] ?? createDefaultSettings(widgetKey, Math.max(0, registryIndex));
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
    const registryIndex = get().widgetRegistry.findIndex((d) => d.key === widgetKey);
    const current = get().userWidgets[widgetKey] ?? createDefaultSettings(widgetKey, Math.max(0, registryIndex));
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
    const registryIndex = get().widgetRegistry.findIndex((d) => d.key === widgetKey);
    const current = get().userWidgets[widgetKey] ?? createDefaultSettings(widgetKey, Math.max(0, registryIndex));
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
    const registryIndex = get().widgetRegistry.findIndex((d) => d.key === widgetKey);
    const current = get().userWidgets[widgetKey] ?? createDefaultSettings(widgetKey, Math.max(0, registryIndex));
    const updated = { ...current, sort_mode: mode, updated_at: Date.now() };
    set({ userWidgets: { ...get().userWidgets, [widgetKey]: updated } });
    try {
      await api.updateWidgetSettings(widgetKey, { sort_mode: mode });
    } catch {
      set({ userWidgets: { ...get().userWidgets, [widgetKey]: current } });
    }
  },

  setQuickAssistantEnabled: async (enabled: boolean) => {
    const currentQA = get().userWidgets['quick_assistant'];
    set({
      quickAssistantEnabled: enabled,
      userWidgets: currentQA
        ? {
            ...get().userWidgets,
            quick_assistant: {
              ...currentQA,
              visibility: enabled ? 'visible' : 'hidden',
              updated_at: Date.now(),
            },
          }
        : get().userWidgets,
    });
    try {
      await api.updateWidgetSettings('quick_assistant', { quick_assistant_enabled: enabled });
    } catch {
      const prevQA = get().userWidgets['quick_assistant'];
      set({
        quickAssistantEnabled: !enabled,
        userWidgets: prevQA
          ? {
              ...get().userWidgets,
              quick_assistant: {
                ...prevQA,
                visibility: !enabled ? 'visible' : 'hidden',
                updated_at: Date.now(),
              },
            }
          : get().userWidgets,
      });
    }
  },

  getActiveWidgets: (capabilities: CapabilityPayload) => {
    const { userWidgets, quickAssistantEnabled, widgetRegistry, isAdmin } = get();
    return widgetRegistry
      .filter((def) => isWidgetVisible(def, {
        userWidgets,
        capabilities,
        quickAssistantEnabled,
        isAdmin,
      }))
      .map((def, index) => ({
        def,
        userSettings: userWidgets[def.key] ?? createDefaultSettings(def.key, index),
        isActive: true,
      }))
      .sort(compareWidgetInstances);
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
    const registryIndex = get().widgetRegistry.findIndex((d) => d.key === widgetKey);
    const current = get().userWidgets[widgetKey] ?? createDefaultSettings(widgetKey, Math.max(0, registryIndex));
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
