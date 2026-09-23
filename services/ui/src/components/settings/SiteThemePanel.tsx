import { useCallback, useEffect, useState } from 'react';
import { useSiteTheme } from '../../themes/siteTheme';
import { themeRegistry } from '../../themes/registry';
import { ThemePackageManager } from '../../themes/ThemePackageManager';

/**
 * Settings UI: pick a website theme from the same pack files the
 * dashboard widgets use. Import/edit/remove packs here too.
 */
export default function SiteThemePanel() {
  const { themeId, setSiteTheme, ready, syncPacksToServer } = useSiteTheme();
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [packVersion, setPackVersion] = useState(0);

  useEffect(() => themeRegistry.subscribe(() => setPackVersion((n) => n + 1)), []);

  const onSelect = useCallback(
    (id: string) => {
      void setSiteTheme(id);
    },
    [setSiteTheme]
  );

  const handleSync = async () => {
    const ok = await syncPacksToServer();
    setSyncMsg(ok ? 'Theme packs synced to your account.' : 'Sync failed — check connection.');
  };

  return (
    <div className="glass-panel rounded-2xl p-4 space-y-3" data-testid="site-theme-panel">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
            Website theme
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            Same theme packages as dashboard widgets. Stored per account.
            Active: <span className="text-purple-400 font-medium">{ready ? themeId : '…'}</span>
          </p>
        </div>
        <button type="button" className="glass-button px-3 py-1.5 text-xs" onClick={handleSync}>
          Sync packs to account
        </button>
      </div>
      {syncMsg && (
        <p className="text-xs text-emerald-400" role="status">
          {syncMsg}
        </p>
      )}
      {/* key forces remount when packs change so the list refreshes */}
      <ThemePackageManager
        key={packVersion}
        selectedThemeId={themeId}
        onSelectTheme={onSelect}
      />
    </div>
  );
}
