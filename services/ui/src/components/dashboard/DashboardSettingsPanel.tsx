import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Capacitor } from '@capacitor/core';
import toast from 'react-hot-toast';
import {
  RotateCcw,
  Download,
  Upload,
  Eye,
  EyeOff,
  Grid3X3,
  Pin,
  PinOff,
  X,
  Settings2,
} from 'lucide-react';
import { useWidgetStore, defaultWidgetDefs } from '../../stores/widgetStore';
import type { UserWidgetSettings, WidgetSize, WidgetVisibility } from '../../types/widget';
import { api } from '../../services/api';

interface DashboardSettingsPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

const WidgetCatalog = () => {
  const { userWidgets, widgetRegistry, showWidget, hideWidget, togglePin } = useWidgetStore();

  return (
    <div className="space-y-1">
      {widgetRegistry.map((def) => {
        const settings = userWidgets[def.key];
        const visibility: WidgetVisibility =
          settings?.visibility ?? (def.key === 'quick_assistant' ? 'hidden' : 'visible');
        const shown = visibility === 'visible';
        const isPinned = settings?.is_pinned ?? false;
        const size = settings?.size ?? def.defaultSize;
        const Icon = def.icon;

        return (
          <div
            key={def.key}
            className="flex items-center justify-between gap-2 px-2 sm:px-3 py-1 sm:py-2 rounded-lg hover:bg-white/5 transition-colors group"
          >
            <div className="flex items-center gap-3 min-w-0">
              <span className="text-slate-500 group-hover:text-slate-300 transition-colors shrink-0">
                <Icon size={14} />
              </span>
              <span className={`text-sm font-medium truncate ${shown ? 'text-white' : 'text-slate-500'}`}>{def.label}</span>
              {/* The size is a desktop nicety; on a phone it squeezed the name
                  down to two letters next to the 44px buttons. */}
              <span className="hidden sm:inline text-[9px] font-bold uppercase tracking-widest text-slate-600 shrink-0">
                {size}
              </span>
            </div>

            <div className="flex items-center gap-1 shrink-0">
              <button
                onClick={() => showWidget(def.key)}
                className="p-1 rounded text-slate-500 hover:text-emerald-400 transition-colors disabled:opacity-30 disabled:hover:text-slate-500 pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:flex pointer-coarse:items-center pointer-coarse:justify-center"
                title="Show"
                aria-label={`Show ${def.label}`}
                disabled={shown}
              >
                <Eye size={13} />
              </button>
              <button
                onClick={() => hideWidget(def.key)}
                className="p-1 rounded text-slate-500 hover:text-red-400 transition-colors disabled:opacity-30 disabled:hover:text-slate-500 pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:flex pointer-coarse:items-center pointer-coarse:justify-center"
                title="Hide"
                aria-label={`Hide ${def.label}`}
                disabled={!shown}
              >
                <EyeOff size={13} />
              </button>
              <button
                onClick={() => togglePin(def.key)}
                className={`p-1 rounded transition-colors pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:flex pointer-coarse:items-center pointer-coarse:justify-center ${
                  isPinned ? 'text-amber-400' : 'text-slate-500 hover:text-amber-400'
                }`}
                title={isPinned ? 'Unpin' : 'Pin to top'}
                aria-label={`${isPinned ? 'Unpin' : 'Pin'} ${def.label}`}
                aria-pressed={isPinned}
              >
                {isPinned ? <PinOff size={13} /> : <Pin size={13} />}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
};

const ExportSection = () => {
  const { userWidgets, widgetRegistry, quickAssistantEnabled } = useWidgetStore();

  const handleExport = useCallback(() => {
    const exportData = {
      version: 1,
      exported_at: new Date().toISOString(),
      quick_assistant_enabled: quickAssistantEnabled,
      widgets: widgetRegistry.map((def) => ({
        ...((userWidgets[def.key]) ?? {
          visibility: 'visible',
          order_index: 0,
          size: def.defaultSize,
          is_pinned: false,
          sort_mode: null,
          pinned_devices: [],
          config: {},
          updated_at: 0,
        }),
        // Last so the registry key always wins over whatever was persisted.
        widget_key: def.key,
      })),
    };

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `jarvis-widget-settings-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [userWidgets, widgetRegistry, quickAssistantEnabled]);

  return (
    <button
      onClick={handleExport}
      className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white glass-button"
    >
      <Download size={14} />
      Export Settings
    </button>
  );
};

const ImportSection = () => {
  const { userWidgets: currentWidgets, setQuickAssistantEnabled, replaceAllWidgets } = useWidgetStore();

  const handleImport = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const text = await file.text();
      const data = JSON.parse(text);

      if (!data.widgets || !Array.isArray(data.widgets)) {
        throw new Error('Invalid format: missing widgets array');
      }

      const SIZES: WidgetSize[] = ['small', 'medium', 'wide', 'tall'];
      const VISIBILITIES: WidgetVisibility[] = ['visible', 'hidden', 'removed'];
      const widgetsMap: Record<string, UserWidgetSettings> = { ...currentWidgets };
      for (const w of data.widgets) {
        if (!w.widget_key || typeof w.widget_key !== 'string') continue;
        const current = currentWidgets[w.widget_key];
        if (!current) continue;
        widgetsMap[w.widget_key] = {
          ...current,
          visibility: VISIBILITIES.includes(w.visibility) ? w.visibility : current.visibility,
          order_index: typeof w.order_index === 'number' ? w.order_index : current.order_index,
          size: SIZES.includes(w.size) ? w.size : current.size,
          is_pinned: typeof w.is_pinned === 'boolean' ? w.is_pinned : current.is_pinned,
          sort_mode: w.sort_mode ?? current.sort_mode,
          pinned_devices: Array.isArray(w.pinned_devices) ? w.pinned_devices : current.pinned_devices,
          config: w.config && typeof w.config === 'object' && !Array.isArray(w.config) ? w.config : current.config,
          updated_at: Date.now(),
        };
      }

      replaceAllWidgets(widgetsMap);

      // Everything that was applied locally goes to the server too: favourite
      // devices and widget config used to apply here and then revert on the
      // next load.
      const results = await Promise.allSettled(
        Object.values(widgetsMap).map((w) =>
          api.updateWidgetSettings(w.widget_key, {
            visibility: w.visibility,
            order_index: w.order_index,
            size: w.size,
            is_pinned: w.is_pinned,
            sort_mode: w.sort_mode,
            pinned_devices: w.pinned_devices,
            config: w.config,
          })
        )
      );
      if (results.some((r) => r.status === 'rejected')) {
        toast.error('Some widget settings could not be saved');
      }

      if (typeof data.quick_assistant_enabled === 'boolean') {
        setQuickAssistantEnabled(data.quick_assistant_enabled);
      }

      toast.success('Widget settings imported');
    } catch (err) {
      console.error('[DashboardSettings] Import failed:', err);
      toast.error(err instanceof Error ? err.message : 'Import failed');
    }
    e.target.value = '';
  }, [currentWidgets, setQuickAssistantEnabled, replaceAllWidgets]);

  return (
    <label className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white glass-button cursor-pointer">
      <Upload size={14} />
      Import Settings
      <input
        type="file"
        accept=".json"
        onChange={handleImport}
        className="hidden"
      />
    </label>
  );
};

const DashboardSettingsPanel = ({ isOpen, onClose }: DashboardSettingsPanelProps) => {
  const { userWidgets, replaceAllWidgets, quickAssistantEnabled, setQuickAssistantEnabled } = useWidgetStore();
  // A blob download does nothing inside the Android WebView, and the layout
  // already follows the account to every device, so file export/import is a
  // browser-only tool.
  const isNative = Capacitor.isNativePlatform();
  const panelRef = useRef<HTMLDivElement>(null);
  const [confirmingReset, setConfirmingReset] = useState(false);

  // Dismissing the dialog must never leave the destructive confirm armed, so
  // every close path goes through here rather than an after-the-fact effect.
  const requestClose = useCallback(() => {
    setConfirmingReset(false);
    onClose();
  }, [onClose]);

  // Dialog behaviour: focus the panel, keep Tab inside it, close on Escape,
  // and hand focus back to whatever opened it.
  useEffect(() => {
    if (!isOpen) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;

    const focusable = () =>
      Array.from(
        panel?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'
        ) ?? []
      );

    (focusable()[0] ?? panel)?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        requestClose();
        return;
      }
      if (e.key !== 'Tab') return;

      const items = focusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;

      if (e.shiftKey && (active === first || !panel?.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      previouslyFocused?.focus?.();
    };
  }, [isOpen, requestClose]);

  const handleReset = useCallback(async () => {
    const defaultByKey = new Map(defaultWidgetDefs.map((def, index) => [def.key, { def, index }]));
    const hasChanges = quickAssistantEnabled || Object.values(userWidgets).some((w) => {
      const entry = defaultByKey.get(w.widget_key as never);
      if (!entry) return false;
      const defaultVisibility = w.widget_key === 'quick_assistant' ? 'hidden' : 'visible';
      return (
        w.visibility !== defaultVisibility ||
        w.is_pinned ||
        w.size !== entry.def.defaultSize ||
        w.order_index !== entry.index
      );
    });
    setConfirmingReset(false);
    if (!hasChanges) {
      toast('Already using the default layout');
      return;
    }

    // Layout only. Favourite devices and per-widget config (climate devices,
    // theme colours for the Android widget) are the user's data, not layout,
    // and the server keeps them anyway, so clearing them here only made the
    // board disagree with itself until the next load.
    const resetWidgets: Record<string, UserWidgetSettings> = {};
    for (let i = 0; i < defaultWidgetDefs.length; i++) {
      const def = defaultWidgetDefs[i];
      const current = userWidgets[def.key];
      resetWidgets[def.key] = {
        widget_key: def.key,
        visibility: def.key === 'quick_assistant' ? 'hidden' : 'visible',
        order_index: i,
        size: def.defaultSize,
        is_pinned: false,
        sort_mode: def.key === 'device_control' ? 'most_used' : null,
        pinned_devices: current?.pinned_devices ?? [],
        config: current?.config ?? {},
        updated_at: Date.now(),
      };
    }
    replaceAllWidgets(resetWidgets);
    if (quickAssistantEnabled) void setQuickAssistantEnabled(false);
    const results = await Promise.allSettled(
      Object.values(resetWidgets).map((w) =>
        api.updateWidgetSettings(w.widget_key, {
          visibility: w.visibility,
          order_index: w.order_index,
          size: w.size,
          is_pinned: w.is_pinned,
          sort_mode: w.sort_mode,
        })
      )
    );
    if (results.some((r) => r.status === 'rejected')) {
      toast.error('Some widgets could not be reset. Try again.');
    } else {
      toast.success('Widget settings reset to defaults');
    }
  }, [userWidgets, replaceAllWidgets, quickAssistantEnabled, setQuickAssistantEnabled]);

  if (!isOpen) return null;

  // Portalled: the dashboard animates in with a transform, and a transformed
  // ancestor turns `fixed` into "fixed to that ancestor", which centred this
  // dialog on the whole page height, far below the viewport.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-3 sm:p-4" onClick={requestClose}>
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dashboard-settings-title"
        className="glass-panel w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col animate-fade-up outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 sm:px-6 py-3 sm:py-4 border-b border-white/5 shrink-0">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-white/5">
              <Settings2 size={16} className="text-purple-400" />
            </div>
            <div>
              <h2 id="dashboard-settings-title" className="text-base font-bold text-white">Dashboard Settings</h2>
              <p className="text-[10px] text-slate-500">Saved to your account and shared by all your devices</p>
            </div>
          </div>
          <button
            onClick={requestClose}
            className="p-2 rounded-lg text-slate-500 hover:text-white hover:bg-white/5 transition-colors min-h-11 min-w-11 flex items-center justify-center"
            aria-label="Close settings"
          >
            <X size={16} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-6">
          {/* Widget Catalog */}
          <section>
            <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
              <Grid3X3 size={14} className="text-purple-400" />
              Widget Catalog
            </h3>
            <div className="glass-card p-1 sm:p-3">
              <WidgetCatalog />
            </div>
          </section>

          {/* Import/Export */}
          {!isNative && (
          <section>
            <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
              <Download size={14} className="text-purple-400" />
              Data Management
            </h3>
            <div className="flex flex-wrap items-center gap-3">
              <ExportSection />
              <ImportSection />
            </div>
          </section>
          )}

          {/* Reset */}
          <section>
            <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
              <RotateCcw size={14} className="text-amber-400" />
              Reset
            </h3>
            <div className="glass-card p-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm text-white font-medium">Reset to defaults</p>
                <p id="reset-all-hint" className="text-xs text-slate-500 mt-0.5">
                  Restore all widgets to their original sizes, positions, and visibility.
                </p>
              </div>
              {confirmingReset ? (
                <div className="flex flex-wrap items-center gap-2" role="alertdialog" aria-labelledby="reset-confirm-title">
                  <p id="reset-confirm-title" className="text-xs text-amber-200 max-w-[16rem]">
                    Reset every widget to its default size, position, and visibility?
                  </p>
                  <button
                    onClick={() => setConfirmingReset(false)}
                    className="px-3 py-2 text-sm text-slate-300 hover:text-white rounded-lg hover:bg-white/5 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleReset}
                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-red-300 border border-red-500/30 bg-red-500/10 hover:bg-red-500/20 rounded-lg transition-colors"
                  >
                    <RotateCcw size={14} />
                    Yes, reset all
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmingReset(true)}
                  aria-describedby="reset-all-hint"
                  className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-amber-300 border border-amber-500/20 hover:bg-amber-500/10 rounded-lg transition-colors"
                >
                  <RotateCcw size={14} />
                  Reset All
                </button>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>,
    document.body,
  );
};

export default DashboardSettingsPanel;
