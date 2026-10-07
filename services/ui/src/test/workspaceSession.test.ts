import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  clearWorkspaceSession,
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
    expect(readWorkspaceSession('sermon')).toEqual(session());
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
