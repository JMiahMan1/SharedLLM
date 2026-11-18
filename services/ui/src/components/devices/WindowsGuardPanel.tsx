import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { Download, Loader2, Monitor } from 'lucide-react';
import { api } from '../../services/api';
import { downloadBlobUrl } from '../../lib/artifactKinds';
import { INSTALLER_ZIP as ZIP, installerDownloads, sizeLabel } from '../../lib/windowsInstaller';

/**
 * The family PC installer (Jarvis Guard, agent/jarvis-guard) for a parent who
 * manages the family's devices: its version and SHA-256, the download, and the
 * short install steps. The server only answers a device manager; for anyone
 * else this panel is not shown at all.
 */

export default function WindowsGuardPanel() {
  const info = useQuery({ queryKey: ['windows-installer'], queryFn: () => api.getWindowsInstaller(), retry: false });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Not a device manager (or signed out): the installer is not theirs to see.
  if (isAxiosError(info.error) && [401, 403].includes(info.error.response?.status ?? 0)) return null;

  const download = async (name: string) => {
    setError(null);
    setBusy(name);
    try {
      downloadBlobUrl(URL.createObjectURL(await api.getWindowsInstallerFile(name)), name);
    } catch {
      setError(`Could not download ${name}. Try again.`);
    } finally {
      setBusy(null);
    }
  };

  const data = info.data;
  const sizes = new Map((data?.files ?? []).map((f) => [f.name, f.size]));

  return (
    <section className="glass-panel overflow-hidden rounded-2xl border border-white/10" data-testid="windows-guard-panel"
      aria-labelledby="windows-guard-title">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 px-4 py-3">
        <h2 id="windows-guard-title" className="flex min-w-0 items-center gap-2.5 text-base font-semibold text-white">
          <Monitor size={18} className="shrink-0 text-sky-400" /> Family PC (Windows)
        </h2>
        {data?.available && data.version && (
          <span className="text-xs text-slate-400" data-testid="windows-guard-version">Jarvis Guard {data.version}</span>
        )}
      </header>
      <div className="space-y-3 p-4 text-sm text-slate-300">
        {info.isLoading && <div className="skeleton h-14 w-full rounded-xl" role="status" aria-label="Loading the Windows installer" />}
        {info.error && <p className="text-amber-300/90">The Windows installer could not be checked.</p>}
        {data && !data.available && (
          <p className="text-slate-400" data-testid="windows-guard-missing">
            There is no Windows installer on the server yet. It is copied over with the next deploy after a Windows build passes.
          </p>
        )}
        {data?.available && (
          <>
            <p>
              Jarvis Guard puts a child's Windows PC under the same screen-time rules as their phone: bedtime, school hours,
              the day's limit, asking for more time, chores and a parent's unlock.
            </p>
            <div className="flex flex-wrap gap-2">
              {installerDownloads(data).map((name, i) => (
                <button key={name} type="button" onClick={() => void download(name)} disabled={busy !== null}
                  className={`inline-flex min-h-11 items-center gap-2 rounded-xl px-4 text-sm font-medium disabled:opacity-60 ${
                    i === 0 ? 'bg-sky-500 text-slate-950 hover:bg-sky-400' : 'border border-white/15 bg-white/5 text-slate-100 hover:bg-white/10'}`}>
                  {busy === name ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
                  {name === ZIP ? 'Download installer' : `Download ${name}`}
                  {sizes.get(name) ? <span className="text-xs opacity-75">({sizeLabel(sizes.get(name) ?? 0)})</span> : null}
                </button>
              ))}
            </div>
            {error && <p className="text-amber-300/90" role="alert">{error}</p>}
            {data.sha256 && (
              <div>
                <p className="text-xs text-slate-400">SHA-256 of jarvis-guard.exe (check it before you run it)</p>
                <code className="mt-1 block select-all break-all rounded-lg bg-black/30 px-3 py-2 font-mono text-xs text-slate-200"
                  data-testid="windows-guard-sha256">{data.sha256}</code>
              </div>
            )}
            <ol className="list-decimal space-y-1.5 pl-5 text-slate-300">
              <li>On the PC, sign in to a Windows administrator account (a parent's), and unzip the download.</li>
              <li>
                Check the file: in PowerShell, <code className="break-all text-xs text-sky-200">Get-FileHash .\jarvis-guard.exe</code> shows
                the SHA-256 above.
              </li>
              <li>
                Open PowerShell as administrator in that folder and run{' '}
                <code className="break-all text-xs text-sky-200">Unblock-File .\*</code> then{' '}
                <code className="break-all text-xs text-sky-200">powershell -ExecutionPolicy Bypass -File .\install.ps1</code>.
              </li>
              <li>It asks for this hub's address, your Jarvis sign-in and the child's Jarvis username.</li>
              <li>
                The installer is not signed, so Windows may say it does not recognise it: choose More info, then Run anyway.
                The child's own Windows account must be a standard user, not an administrator.
              </li>
            </ol>
          </>
        )}
      </div>
    </section>
  );
}
