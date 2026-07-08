import { useCallback } from 'react';
import {
  RotateCcw,
  Download,
  Upload,
  Eye,
  EyeOff,
  Grid3X3,
  X,
  Settings2,
} from 'lucide-react';
import { useWidgetStore, defaultWidgetDefs } from '../stores/widgetStore';
import type { UserWidgetSettings, WidgetVisibility } from '../types/widget';

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

        return (
          <div
            key={def.key}
            className="flex items-center justify-between px-3 py-2 rounded-lg hover:bg-white/5 transition-colors group"
          >
            <div className="flex items-center gap-3 min-w-0">
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
                className="p-1 rounded text-slate-500 hover:text-amber-400 transition-colors"
                title={isPinned ? 'Unpin' : 'Pin'}
                aria-label={`${isPinned ? 'Unpin' : 'Pin'} ${def.label}`}
              >
                <Grid3X3 size={13} className={isPinned ? 'text-amber-400' : ''} />
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
        widget_key: def.key,
        ...((userWidgets[def.key]) ?? {
          widget_key: def.key,
          visibility: 'visible',
          order_index: 0,
          size: def.defaultSize,
          is_pinned: false,
          sort_mode: null,
          pinned_devices: [],
          config: {},
          updated_at: 0,
        }),
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
  const { userWidgets: currentWidgets, setQuickAssistantEnabled, syncWithServer } = useWidgetStore();

  const handleImport = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const text = await file.text();
      const data = JSON.parse(text);

      if (!data.widgets || !Array.isArray(data.widgets)) {
        throw new Error('Invalid format: missing widgets array');
      }

      for (const w of data.widgets) {
        if (!w.widget_key) continue;
        if (typeof w.widget_key !== 'string') continue;

        const current = currentWidgets[w.widget_key];
        if (!current) continue;

        await syncWithServer();
      }

      const widgetsMap: Record<string, UserWidgetSettings> = {};
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

      useWidgetStore.setState({ userWidgets: widgetsMap });

      if (typeof data.quick_assistant_enabled === 'boolean') {
        setQuickAssistantEnabled(data.quick_assistant_enabled);
      }

      await syncWithServer();
    } catch (err) {
      console.error('[DashboardSettings] Import failed:', err);
    }
  }, [currentWidgets, setQuickAssistantEnabled, syncWithServer]);

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
  const { userWidgets, syncWithServer } = useWidgetStore();

  const handleReset = useCallback(async () => {
    const hasChanges = Object.values(userWidgets).some(
      (w) => w.visibility !== 'visible' || w.is_pinned || w.size !== 'medium'
    );
    if (!hasChanges) return;

    const confirm = window.confirm('Reset all widget settings to defaults? This cannot be undone.');
    if (!confirm) return;

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
    useWidgetStore.setState({ userWidgets: resetWidgets });
    await syncWithServer();
  }, [userWidgets, syncWithServer]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onClose}>
      <div
        className="glass-panel w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col animate-fade-up"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/5 shrink-0">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-white/5">
              <Settings2 size={16} className="text-purple-400" />
            </div>
            <div>
              <h2 className="text-base font-bold text-white">Dashboard Settings</h2>
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
                <p className="text-xs text-slate-500 mt-0.5">
                  Restore all widgets to their original sizes, positions, and visibility.
                </p>
              </div>
              <button
                onClick={handleReset}
                className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-amber-300 border border-amber-500/20 hover:bg-amber-500/10 rounded-lg transition-colors"
              >
                <RotateCcw size={14} />
                Reset All
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
};

export default DashboardSettingsPanel;
