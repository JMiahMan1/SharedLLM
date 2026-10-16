import { describe, expect, it } from 'vitest';
import { eventTime, logText, missionLogText, parseMissionLog } from './missionLog';

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
});
