import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  clearWorkspaceSession,
  hydrateWorkspaceSessions,
  mergeWorkspaceSessions,
  normaliseWorkspaceSession,
  readWorkspaceSession,
  writeWorkspaceSession,
  type WorkspaceSession,
} from '../components/workspace/workspaceSession';

const KEY = 'jarvis_workspace_session_v1';

function session(over: Partial<WorkspaceSession> = {}): WorkspaceSession {
  return {
    view: 'git',
    currentPath: 'Bible Study',
    terminalOpen: true,
    terminalPosition: 'sidebar',
    terminalHeight: 320,
    activeTab: 'sermon.md',
    tabs: [
      { path: 'sermon.md', kind: 'markdown', dirty: false },
      { path: 'notes.txt', kind: 'text', dirty: true, content: 'half a thought', language: 'plaintext' },
    ],
    ...over,
  };
}

describe('the workspace session', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('comes back exactly as it was left', () => {
    writeWorkspaceSession('sermon', session());
    const restored = readWorkspaceSession('sermon');
    expect(restored).toMatchObject(session());
    // Saved with a timestamp so a browser copy and a server copy can be
    // compared; the session itself is unchanged by it.
    expect(typeof restored?.savedAt).toBe('number');
  });

  it('keeps each workspace apart', () => {
    writeWorkspaceSession('sermon', session());
    writeWorkspaceSession('home-work', session({ view: 'explorer', tabs: [] }));
    expect(readWorkspaceSession('sermon')?.view).toBe('git');
    expect(readWorkspaceSession('home-work')?.view).toBe('explorer');
    expect(readWorkspaceSession('home-work')?.tabs).toEqual([]);
  });

  it('has nothing to restore the first time', () => {
    expect(readWorkspaceSession('sermon')).toBeNull();
  });

  it('keeps an unsaved buffer so typed text is not thrown away', () => {
    writeWorkspaceSession('sermon', session());
    const restored = readWorkspaceSession('sermon');
    const dirty = restored?.tabs.find((t) => t.path === 'notes.txt');
    expect(dirty?.dirty).toBe(true);
    expect(dirty?.content).toBe('half a thought');
  });

  it('drops a buffer too large to store but keeps the rest of the session', () => {
    const huge = 'x'.repeat(600 * 1024);
    writeWorkspaceSession(
      'sermon',
      session({ tabs: [{ path: 'big.log', kind: 'text', dirty: true, content: huge }] }),
    );
    const restored = readWorkspaceSession('sermon');
    expect(restored?.tabs.map((t) => t.path)).toEqual(['big.log']);
    expect(restored?.tabs[0].content).toBeUndefined();
    expect(restored?.currentPath).toBe('Bible Study');
  });

  it('starts clean rather than throwing when the stored entry is corrupt', () => {
    localStorage.setItem(KEY, 'not json at all');
    expect(readWorkspaceSession('sermon')).toBeNull();
  });

  it('drops only the fields it cannot trust', () => {
    localStorage.setItem(
      KEY,
      JSON.stringify({
        sermon: {
          view: 'nonsense',
          currentPath: '',
          terminalOpen: 'yes',
          terminalPosition: 'left',
          terminalHeight: 'tall',
          activeTab: 'gone.txt',
          tabs: [
            { path: 'kept.txt', kind: 'text', dirty: false },
            { kind: 'text' },
            'not an object',
          ],
        },
      }),
    );
    const restored = readWorkspaceSession('sermon');
    expect(restored).not.toBeNull();
    expect(restored?.view).toBe('explorer');
    expect(restored?.currentPath).toBe('.');
    expect(restored?.terminalOpen).toBe(false);
    expect(restored?.terminalPosition).toBe('bottom');
    expect(restored?.terminalHeight).toBe(250);
    expect(restored?.activeTab).toBeNull();
    expect(restored?.tabs.map((t) => t.path)).toEqual(['kept.txt']);
  });

  it('forgets a workspace when asked to', () => {
    writeWorkspaceSession('sermon', session());
    clearWorkspaceSession('sermon');
    expect(readWorkspaceSession('sermon')).toBeNull();
  });
});

describe('choosing between the browser copy and the server copy', () => {
  it('keeps the newer local session so an offline device is not reverted', () => {
    const local = session({ view: 'tools', savedAt: 5000 });
    const remote = session({ view: 'git', savedAt: 1000 });
    expect(mergeWorkspaceSessions(local, remote)?.view).toBe('tools');
  });

  it('takes the server session when it is newer', () => {
    const local = session({ view: 'tools', savedAt: 1000 });
    const remote = session({ view: 'git', savedAt: 5000 });
    expect(mergeWorkspaceSessions(local, remote)?.view).toBe('git');
  });

  it('prefers the shared copy when both were saved at the same moment', () => {
    const local = session({ view: 'tools', savedAt: 3000 });
    const remote = session({ view: 'git', savedAt: 3000 });
    expect(mergeWorkspaceSessions(local, remote)?.view).toBe('git');
  });

  it('has nothing to merge when neither copy exists', () => {
    expect(mergeWorkspaceSessions(null, null)).toBeNull();
  });

  it('uses whichever copy exists when only one does', () => {
    const local = session({ view: 'tools', savedAt: 1000 });
    const remote = session({ view: 'git', savedAt: 1000 });
    expect(mergeWorkspaceSessions(local, null)?.view).toBe('tools');
    expect(mergeWorkspaceSessions(null, remote)?.view).toBe('git');
  });
});

describe('a session read back from the server', () => {
  it('is validated the same way as one from storage', () => {
    const normalised = normaliseWorkspaceSession({ ...session(), view: 'nonsense', savedAt: 'yesterday' });
    expect(normalised?.view).toBe('explorer');
    expect(normalised?.savedAt).toBe(0);
  });

  it('defaults the timestamp when the payload does not carry one', () => {
    const withoutStamp: Record<string, unknown> = { ...session() };
    delete withoutStamp.savedAt;
    expect(normaliseWorkspaceSession(withoutStamp)?.savedAt).toBe(0);
  });

  it('refuses a payload that is not an object', () => {
    expect(normaliseWorkspaceSession('nope')).toBeNull();
    expect(normaliseWorkspaceSession(null)).toBeNull();
    expect(normaliseWorkspaceSession(7)).toBeNull();
  });

  it('is written into storage so the first paint after opening is right', () => {
    hydrateWorkspaceSessions({ sermon: { ...session({ savedAt: 1234 }) } });
    const restored = readWorkspaceSession('sermon');
    expect(restored?.view).toBe('git');
    expect(restored?.currentPath).toBe('Bible Study');
    expect(restored?.savedAt).toBe(1234);
  });

  it('ignores an entry it cannot read instead of losing the others', () => {
    hydrateWorkspaceSessions({ sermon: 'not a session', 'home-work': { ...session() } });
    expect(readWorkspaceSession('sermon')).toBeNull();
    expect(readWorkspaceSession('home-work')?.view).toBe('git');
  });
});
