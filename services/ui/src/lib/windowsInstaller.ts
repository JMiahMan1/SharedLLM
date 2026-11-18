import type { WindowsInstallerInfo } from '../types/api';

/** The family PC installer's one-file download, when the server has it (scripts/deploy_remote.sh makes it). */
export const INSTALLER_ZIP = 'jarvis-guard-windows.zip';

/** The download a parent should take: the zip when the server has it, else the exe and installer separately. */
export function installerDownloads(info: WindowsInstallerInfo): string[] {
  const names = info.files.map((f) => f.name);
  if (names.includes(INSTALLER_ZIP)) return [INSTALLER_ZIP];
  return ['jarvis-guard.exe', 'install.ps1', 'uninstall.ps1'].filter((n) => names.includes(n));
}

export function sizeLabel(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${Math.round(bytes / (1024 * 1024))} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} bytes`;
}
