import { useCallback, useEffect, useRef, useState } from 'react';
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
import type { UserWidgetSettings, WidgetVisibility } from '../../types/widget';
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
        const visibility: WidgetVisibility = settings?.visibility ?? 'visible';
        const isPinned = settings?.is_pinned ?? false;
        const size = settings?.size ?? def.defaultSize;
        const Icon = def.icon;

        return (
          <div
            key={def.key}
            className="flex items-center justify-between px-3 py-2 rounded-lg hover:bg-white/5 transition-colors group"
          >
            <div className="flex items-center gap-3 min-w-0">
              <span className="text-slate-500 group-hover:text-slate-300 transition-colors shrink-0">
                <Icon size={14} />
              </span>
              <span className="text-sm font-medium text-white truncate">{def.label}</span>
              <span className="text-[9px] font-bold uppercase tracking-widest text-slate-600 shrink-0">
                {size}
              </span>
            </div>

            <div className="flex items-center gap-1 shrink-0">
              <button
                onClick={() => showWidget(def.key)}
                className="p-1 rounded text-slate-500 hover:text-emerald-400 transition-colors"
                title="Show"
                aria-label={`Show ${def.label}`}
              >
                <Eye size={13} />
              </button>
              <button
                onClick={() => hideWidget(def.key)}
                className="p-1 rounded text-slate-500 hover:text-red-400 transition-colors"
                title="Hide"
                aria-label={`Hide ${def.label}`}
                disabled={visibility === 'hidden'}
              >
                <EyeOff size={13} />
              </button>
              <button
                onClick={() => togglePin(def.key)}
                className={`p-1 rounded transition-colors ${
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

      const widgetsMap: Record<string, UserWidgetSettings> = { ...currentWidgets };
      for (const w of data.widgets) {
        if (!w.widget_key || typeof w.widget_key !== 'string') continue;
        const current = currentWidgets[w.widget_key];
        if (!current) continue;
        widgetsMap[w.widget_key] = {
          ...current,
          visibility: (w.visibility as WidgetVisibility) ?? current.visibility,
          order_index: typeof w.order_index === 'number' ? w.order_index : current.order_index,
          size: (w.size as never) ?? current.size,
          is_pinned: typeof w.is_pinned === 'boolean' ? w.is_pinned : current.is_pinned,
          sort_mode: w.sort_mode ?? current.sort_mode,
          pinned_devices: Array.isArray(w.pinned_devices) ? w.pinned_devices : current.pinned_devices,
          config: typeof w.config === 'object' ? w.config : current.config,
          updated_at: Date.now(),
        };
      }

      replaceAllWidgets(widgetsMap);

      await Promise.all(
        Object.values(widgetsMap).map((w) =>
          api.updateWidgetSettings(w.widget_key as never, {
            visibility: w.visibility,
            order_index: w.order_index,
            size: w.size,
            is_pinned: w.is_pinned,
            sort_mode: w.sort_mode,
          }).catch(() => undefined)
        )
      );

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
  const { userWidgets, replaceAllWidgets } = useWidgetStore();
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
    const defaultByKey = new Map(defaultWidgetDefs.map((def) => [def.key, def]));
    const hasChanges = Object.values(userWidgets).some((w) => {
      const def = defaultByKey.get(w.widget_key as never);
      const defaultSize = def?.defaultSize ?? 'medium';
      const defaultVisibility = w.widget_key === 'quick_assistant' ? 'hidden' : 'visible';
      return w.visibility !== defaultVisibility || w.is_pinned || w.size !== defaultSize;
    });
    if (!hasChanges) return;

    const resetWidgets: Record<string, UserWidgetSettings> = {};
    for (let i = 0; i < defaultWidgetDefs.length; i++) {
      const def = defaultWidgetDefs[i];
      resetWidgets[def.key] = {
        widget_key: def.key,
        visibility: def.key === 'quick_assistant' ? 'hidden' : 'visible',
        order_index: i,
        size: def.defaultSize,
        is_pinned: false,
        sort_mode: def.key === 'device_control' ? 'most_used' : null,
        pinned_devices: [],
        config: {},
        updated_at: Date.now(),
      };
    }
    replaceAllWidgets(resetWidgets);
    await Promise.all(
      Object.values(resetWidgets).map((w) =>
        api.updateWidgetSettings(w.widget_key as never, {
          visibility: w.visibility,
          order_index: w.order_index,
          size: w.size,
          is_pinned: w.is_pinned,
          sort_mode: w.sort_mode,
        }).catch(() => undefined)
      )
    );
    toast.success('Widget settings reset to defaults');
    setConfirmingReset(false);
  }, [userWidgets, replaceAllWidgets]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={requestClose}>
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
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/5 shrink-0">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-white/5">
              <Settings2 size={16} className="text-purple-400" />
            </div>
            <div>
              <h2 id="dashboard-settings-title" className="text-base font-bold text-white">Dashboard Settings</h2>
              <p className="text-[10px] text-slate-500">Widget catalog, visibility, and preferences</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg text-slate-500 hover:text-white hover:bg-white/5 transition-colors"
            aria-label="Close settings"
          >
            <X size={16} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Widget Catalog */}
          <section>
            <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
              <Grid3X3 size={14} className="text-purple-400" />
              Widget Catalog
            </h3>
            <div className="glass-card p-3">
              <WidgetCatalog />
            </div>
          </section>

          {/* Import/Export */}
          <section>
            <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
              <Download size={14} className="text-purple-400" />
              Data Management
            </h3>
            <div className="flex items-center gap-3">
              <ExportSection />
              <ImportSection />
            </div>
          </section>

          {/* Reset */}
          <section>
            <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
              <RotateCcw size={14} className="text-amber-400" />
              Reset
            </h3>
            <div className="glass-card p-4 flex items-center justify-between">
              <div>
                <p className="text-sm text-white font-medium">Reset to defaults</p>
                <p id="reset-all-hint" className="text-xs text-slate-500 mt-0.5">
                  Restore all widgets to their original sizes, positions, and visibility.
                </p>
              </div>
              {confirmingReset ? (
                <div className="flex items-center gap-2" role="alertdialog" aria-labelledby="reset-confirm-title">
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
    </div>
  );
};

export default DashboardSettingsPanel;
