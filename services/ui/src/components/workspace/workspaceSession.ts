/**
 * Where the reader left off in a workspace: which view, which folder, which
 * files were open, and how the terminal was arranged.
 *
 * Kept in the browser on purpose -- this is a place in the UI, not workspace
 * content, and it must never be sent anywhere. An entry that cannot be read
 * starts a clean session rather than blocking the workspace, but the reason is
 * logged so a corrupt entry is not invisible.
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

function normalise(raw: unknown): WorkspaceSession | null {
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
  };
}

export function readWorkspaceSession(workspaceId: string): WorkspaceSession | null {
  const entry = readAll()[workspaceId];
  if (!entry) return null;
  const session = normalise(entry);
  if (!session) {
    console.warn(`[workspace] the saved session for "${workspaceId}" was not readable.`);
  }
  return session;
}

/**
 * Save the session. A dirty buffer past {@link MAX_SAVED_CONTENT} is dropped
 * from the saved copy first: the tab layout is worth keeping even when one
 * buffer is too large to store, and silently losing everything would be worse.
 */
export function writeWorkspaceSession(workspaceId: string, session: WorkspaceSession): void {
  const trimmed: WorkspaceSession = {
    ...session,
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
