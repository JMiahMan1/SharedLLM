import { describe, expect, it } from 'vitest';
import {
  actionToolName,
  eventTime,
  isTelemetryEvent,
  lintVerdict,
  logText,
  missionLogText,
  missionToolLabel,
  parseMissionLog,
  summarizeMissionEvents,
  telemetryLabel,
} from './missionLog';

describe('missionLog', () => {
  it('reads a stored JSON log, and plain text as one line', () => {
    expect(parseMissionLog('[{"type":"system","data":"hi"}]')).toEqual([{ type: 'system', data: 'hi' }]);
    expect(parseMissionLog('plain old log')).toEqual([{ type: 'system', data: 'plain old log' }]);
    expect(parseMissionLog(null)).toEqual([]);
  });

  it('never hands React an object', () => {
    expect(logText({ text: 'part' })).toBe('part');
    expect(logText({ a: 1 })).toBe('{"a":1}');
    expect(logText(null)).toBe('');
  });

  it('only shows a time the server recorded', () => {
    expect(eventTime({ data: 'x' })).toBe('');
    expect(eventTime({ data: 'x', timestamp: 0 })).not.toBe('');
  });

  it('writes a download with the result at the end', () => {
    const text = missionLogText([{ type: 'action', data: 'run' }], 'done');
    expect(text).toBe('[action] run\n\n--- MISSION RESULT ---\ndone');
    expect(missionLogText([])).toBe('No logs available.');
  });

  it('recognises tool names and cross-harness handoffs', () => {
    expect(actionToolName({ type: 'action', data: 'Executing Tool: WorkspaceLintRequest' })).toBe('WorkspaceLintRequest');
    expect(actionToolName({ type: 'action', data: 'Calling tool: DelegateRavenRequest' })).toBe('DelegateRavenRequest');
    expect(actionToolName({ type: 'action_payload', data: '{"@type":"DelegateSubagentRequest","task":"x"}' })).toBe(
      'DelegateSubagentRequest',
    );
    expect(actionToolName({ type: 'action', data: 'Just thinking out loud' })).toBeNull();

    expect(missionToolLabel('DelegateSubagentRequest')).toBe('Explorer subagent');
    expect(missionToolLabel('DelegateRavenRequest')).toBe('Raven mission handoff');
    expect(missionToolLabel('WorkspaceLintRequest')).toBe('Lint verification');
    expect(missionToolLabel('WorkspaceSearchRequest')).toBe('Workspace search');
    expect(missionToolLabel(null)).toBe('Tool');
  });

  it('reads the lint verdict from a result line', () => {
    expect(lintVerdict({ type: 'result_success', data: 'Post-write lint clean' })).toBe('passed');
    expect(lintVerdict({ type: 'result_error', data: 'LINT FAILED for src/a.ts: missing import' })).toBe('failed');
    expect(lintVerdict({ type: 'result_error', data: 'LINT UNVERIFIED for src/a.ts' })).toBe('unverified');
    expect(lintVerdict({ type: 'result_success', data: 'Wrote the file' })).toBeNull();
  });

  it('treats SIM, observation masking, compaction and fuel as telemetry', () => {
    expect(isTelemetryEvent({ type: 'sim_memory', data: 'Lesson promoted' })).toBe(true);
    expect(isTelemetryEvent({ type: 'system', data: 'SIM lesson promoted: read lint output' })).toBe(true);
    expect(isTelemetryEvent({ type: 'observation_mask', data: 'masked 12 messages' })).toBe(true);
    expect(isTelemetryEvent({ type: 'system', data: 'Context compacted to 40%' })).toBe(true);
    expect(isTelemetryEvent({ type: 'system', data: 'Hard-compact started' })).toBe(true);
    expect(isTelemetryEvent({ type: 'reasoning', data: 'Plan the work' })).toBe(false);

    expect(telemetryLabel({ type: 'sim', data: 'anything' })).toBe('SIM');
    expect(telemetryLabel({ type: 'observation_mask', data: 'anything' })).toBe('Observation masked');
    expect(telemetryLabel({ type: 'compaction', data: 'anything' })).toBe('Context compacted');
    expect(telemetryLabel({ type: 'fuel', data: 'anything' })).toBe('Fuel');
  });

  it('folds an action, its payload, and its result into one readable step', () => {
    const steps = summarizeMissionEvents([
      { type: 'action', data: 'Executing Tool: DelegateSubagentRequest' },
      { type: 'action_payload', data: '{"task":"Explore the repository"}' },
      { type: 'result_success', data: 'Explorer found three files.' },
      { type: 'action', data: 'Executing Tool: WorkspaceFileWriteRequest' },
      { type: 'result_success', data: 'Post-write lint clean' },
      { type: 'system', data: 'SIM lesson promoted: read lint output' },
    ]);

    expect(steps).toHaveLength(3);
    expect(steps[0]).toMatchObject({
      kind: 'subagent',
      label: 'Explorer subagent',
      detail: 'Explorer found three files.',
      status: 'success',
      payload: '{"task":"Explore the repository"}',
    });
    expect(steps[0].events).toHaveLength(3);
    expect(steps[1]).toMatchObject({ kind: 'lint', label: 'Lint verification', status: 'success' });
    expect(steps[2]).toMatchObject({ kind: 'telemetry', label: 'SIM', status: 'info' });
  });

  it('captures failed lint, parallel batches, and standalone lint results', () => {
    const steps = summarizeMissionEvents([
      { type: 'action', data: 'Executing Tool: WorkspaceLintRequest' },
      { type: 'result_error', data: 'LINT FAILED for src/a.ts: missing import' },
      { type: 'action', data: 'Executing Tool: ParallelReadRequest' },
      { type: 'result_success', data: 'Read 3 files in parallel' },
      { type: 'result_success', data: 'Post-write lint clean' },
    ]);

    expect(steps[0]).toMatchObject({ kind: 'lint', status: 'error', detail: 'LINT FAILED for src/a.ts: missing import' });
    expect(steps[1]).toMatchObject({ kind: 'parallel', status: 'success', label: 'Parallel read batch' });
    expect(steps[2]).toMatchObject({ kind: 'lint', status: 'success', label: 'Lint verification' });
  });

  it('keeps unknown events, including old actions, visible and harmless', () => {
    const steps = summarizeMissionEvents([
      { type: 'system', data: 'plain old log' },
      { type: 'action', data: 'Something happened without a tool name' },
      { type: 'custom_event', data: { text: 'still readable' } },
    ]);

    expect(steps[0]).toMatchObject({ kind: 'event', type: 'system', detail: 'plain old log' });
    expect(steps[1]).toMatchObject({ kind: 'tool', label: 'Tool', detail: 'Something happened without a tool name', status: 'running' });
    expect(steps[2]).toMatchObject({ kind: 'event', type: 'custom_event', detail: 'still readable' });
  });
});
