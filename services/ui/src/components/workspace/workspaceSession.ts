/**
 * Where the reader left off in a workspace: which view, which folder, which
 * files were open, and how the terminal was arranged.
 *
 * Kept in the browser so the first paint after a reload is right, and saved on
 * the server for the reader's own account so opening the same workspace on
 * another device lands in the same place. It is never shared with anyone else
 * -- it is a place in the UI, not workspace content. Unsaved buffers travel
 * with it, which is the point: a half-typed file should survive a closed
 * browser or a second device.
 *
 * Every entry is validated on the way in, whether it came from storage or from
 * the server, because neither is trusted to be well formed. An entry that
 * cannot be read starts a clean session rather than blocking the workspace,
 * but the reason is logged so a corrupt entry is not invisible.
 */
export const WORKSPACE_VIEWS = ['explorer', 'git', 'tools', 'chat', 'terminal'] as const;
export type WorkspaceView = (typeof WORKSPACE_VIEWS)[number];

export interface WorkspaceSessionTab {
  path: string;
  kind: string;
  dirty: boolean;
  /** Present only for a tab with unsaved edits, so typed text is not lost. */
  content?: string;
  language?: string;
}

export interface WorkspaceSession {
  view: WorkspaceView;
  currentPath: string;
  terminalOpen: boolean;
  terminalPosition: 'sidebar' | 'bottom';
  terminalHeight: number;
  activeTab: string | null;
  tabs: WorkspaceSessionTab[];
  /**
   * When this session was last written, in epoch milliseconds. It exists so a
   * browser copy and a server copy can be compared: the newer one wins, and
   * the server copy wins a tie because it is the one both devices share.
   * Absent counts as zero, and {@link writeWorkspaceSession} stamps it.
   */
  savedAt?: number;
}

const STORAGE_KEY = 'jarvis_workspace_session_v1';

/**
 * A dirty buffer larger than this is not written. Unsaved text is worth a lot,
 * but the whole session is worth more, and a multi-megabyte string would fail
 * the browser's storage quota and take the tab layout down with it.
 */
const MAX_SAVED_CONTENT = 512 * 1024;

let warnedAboutStorage = false;

type SessionMap = Record<string, WorkspaceSession>;

function readAll(): SessionMap {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as SessionMap) : {};
  } catch (error) {
    if (!warnedAboutStorage) {
      warnedAboutStorage = true;
      console.warn('[workspace] could not read the saved session:', error);
    }
    return {};
  }
}

function writeAll(sessions: SessionMap): boolean {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
    return true;
  } catch (error) {
    if (!warnedAboutStorage) {
      warnedAboutStorage = true;
      console.warn('[workspace] could not save the session:', error);
    }
    return false;
  }
}

function normaliseTab(raw: unknown): WorkspaceSessionTab | null {
  if (!raw || typeof raw !== 'object') return null;
  const tab = raw as Record<string, unknown>;
  if (typeof tab.path !== 'string' || !tab.path) return null;
  if (typeof tab.kind !== 'string' || !tab.kind) return null;
  const content = typeof tab.content === 'string' ? tab.content : undefined;
  return {
    path: tab.path,
    kind: tab.kind,
    dirty: tab.dirty === true,
    ...(content !== undefined ? { content } : {}),
    ...(typeof tab.language === 'string' ? { language: tab.language } : {}),
  };
}

/**
 * Validate anything claiming to be a session, from the browser or the server,
 * substituting only the field that is wrong. A server that one day sends
 * something unexpected costs the reader a default, not their tab layout.
 */
export function normaliseWorkspaceSession(raw: unknown): WorkspaceSession | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;
  const view = WORKSPACE_VIEWS.includes(data.view as WorkspaceView)
    ? (data.view as WorkspaceView)
    : 'explorer';
  const tabs = Array.isArray(data.tabs)
    ? data.tabs.map(normaliseTab).filter((t): t is WorkspaceSessionTab => t !== null)
    : [];
  const activeTab =
    typeof data.activeTab === 'string' && tabs.some((t) => t.path === data.activeTab)
      ? data.activeTab
      : null;
  return {
    view,
    currentPath: typeof data.currentPath === 'string' && data.currentPath ? data.currentPath : '.',
    terminalOpen: data.terminalOpen === true,
    terminalPosition: data.terminalPosition === 'sidebar' ? 'sidebar' : 'bottom',
    terminalHeight:
      typeof data.terminalHeight === 'number' && Number.isFinite(data.terminalHeight)
        ? Math.max(100, Math.min(2000, data.terminalHeight))
        : 250,
    activeTab,
    tabs,
    savedAt:
      typeof data.savedAt === 'number' && Number.isFinite(data.savedAt) ? data.savedAt : 0,
  };
}

/**
 * Pick between the copy in this browser and the copy on the server.
 *
 * Returns null only when there is nothing to restore. A tie goes to the remote
 * copy because it is the one every device shares, and an unknown timestamp
 * counts as zero so a session saved before this existed still loses to one
 * that carries a time.
 */
export function mergeWorkspaceSessions(
  local: WorkspaceSession | null,
  remote: WorkspaceSession | null,
): WorkspaceSession | null {
  if (!local) return remote;
  if (!remote) return local;
  return (remote.savedAt ?? 0) >= (local.savedAt ?? 0) ? remote : local;
}

export function readWorkspaceSession(workspaceId: string): WorkspaceSession | null {
  const entry = readAll()[workspaceId];
  if (!entry) return null;
  const session = normaliseWorkspaceSession(entry);
  if (!session) {
    console.warn(`[workspace] the saved session for "${workspaceId}" was not readable.`);
  }
  return session;
}

/**
 * Write sessions the server knows about into the browser copy, so the first
 * paint after a reload is the right one even before the next fetch lands. The
 * server's copy wins outright here: it was just read, so it is the freshest
 * thing this device has seen.
 */
export function hydrateWorkspaceSessions(entries: Record<string, unknown>): void {
  const sessions = readAll();
  let changed = false;
  for (const [workspaceId, raw] of Object.entries(entries)) {
    const session = normaliseWorkspaceSession(raw);
    if (!session) continue;
    sessions[workspaceId] = session;
    changed = true;
  }
  if (changed) writeAll(sessions);
}

/**
 * Save the session. A dirty buffer past {@link MAX_SAVED_CONTENT} is dropped
 * from the saved copy first: the tab layout is worth keeping even when one
 * buffer is too large to store, and silently losing everything would be worse.
 */
export function writeWorkspaceSession(workspaceId: string, session: WorkspaceSession): void {
  const trimmed: WorkspaceSession = {
    ...session,
    savedAt: Date.now(),
    tabs: session.tabs.map((tab) =>
      tab.content !== undefined && tab.content.length > MAX_SAVED_CONTENT
        ? { ...tab, content: undefined }
        : tab,
    ),
  };
  const sessions = readAll();
  sessions[workspaceId] = trimmed;
  writeAll(sessions);
}

export function clearWorkspaceSession(workspaceId: string): void {
  const sessions = readAll();
  if (!(workspaceId in sessions)) return;
  delete sessions[workspaceId];
  writeAll(sessions);
}
