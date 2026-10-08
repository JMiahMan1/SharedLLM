import { describe, expect, it } from 'vitest';
import { emptyTurn, foldEvent, toolLabel, toolSummary } from './chatParts';
import type { WorkspaceChatEvent } from '../../../types/api';

const fold = (events: WorkspaceChatEvent[]) => events.reduce(foldEvent, emptyTurn());

describe('chat turn folding', () => {
  it('keeps thinking per step, and turns an announced tool call into prose plus a card', () => {
    const turn = fold([
      { type: 'start', requested_mode: 'auto', resolved_mode: 'single_task', reason: 'a task' },
      { type: 'step', n: 1 },
      { type: 'thinking', text: 'Find ', step: 1 },
      { type: 'thinking', text: 'the file.', step: 1 },
      { type: 'text', text: 'Let me look.\n```json\n{"tool"', step: 1 },
      { type: 'tool_call', id: 's1', name: 'WorkspaceSearchRequest', input: { query: 'mkv' }, preamble: 'Let me look.' },
      { type: 'tool_result', id: 's1', output: 'File names that match:\n- a.mkv' },
      { type: 'step', n: 'final' },
      { type: 'text', text: 'It is a.mkv.', step: 'final' },
    ]);
    expect(turn.meta.resolved_mode).toBe('single_task');
    expect(turn.parts.map((p) => p.type)).toEqual(['reasoning', 'text', 'tool']);
    expect(turn.parts[0]).toMatchObject({ text: 'Find the file.' });
    expect(turn.parts[2]).toMatchObject({ status: 'done', output: expect.stringContaining('a.mkv') });
    expect(turn.liveText).toBe('It is a.mkv.');
  });

  it('marks a refused tool as an error', () => {
    const turn = fold([
      { type: 'tool_call', id: 's1', name: 'WorkspaceFileReadRequest', input: { path: 'x' } },
      { type: 'tool_result', id: 's1', output: "Sorry, I couldn't complete that action: File not found" },
    ]);
    expect(turn.parts[0]).toMatchObject({ status: 'error' });
  });

  it('records a Raven mission', () => {
    const turn = fold([{ type: 'mission', mission_id: 42 }]);
    expect(turn.parts).toEqual([{ type: 'mission', mission_id: 42, status: 'queued' }]);
    expect(turn.meta.mission_id).toBe(42);
  });

  it('labels tools for people', () => {
    expect(toolLabel('WorkspaceSearchRequest')).toBe('Workspace search');
    expect(toolSummary({ user: 'x', query: 'sermon notes' })).toBe('sermon notes');
  });
});
