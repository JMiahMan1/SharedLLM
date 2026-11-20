import { describe, expect, it } from 'vitest';
import { levelLetter, logText, matches, mergeItems, shownAt, type LogItem } from './deviceLogs';

const line = (seq: number, level: 'error' | 'warning' | 'info' | 'config' | 'debug' | 'verbose', message: string, tag = 'sensor'): LogItem =>
  ({ type: 'line', seq, at: '2026-01-02T03:04:05.006+00:00', level, tag, message });
const marker = (seq: number | null, event: string, message: string): LogItem =>
  ({ type: 'marker', seq, at: '2026-01-02T03:04:06.000+00:00', event, message });

describe('deviceLogs', () => {
  it('shows a line only at its level or a more detailed view; markers always', () => {
    expect(shownAt(line(1, 'debug', 'x'), 'info')).toBe(false);
    expect(shownAt(line(1, 'config', 'x'), 'info')).toBe(true);
    expect(shownAt(line(1, 'verbose', 'x'), 'verbose')).toBe(true);
    expect(shownAt(line(1, 'warning', 'x'), 'error')).toBe(false);
    expect(shownAt(marker(2, 'offline', 'Device offline'), 'error')).toBe(true);
  });

  it('searches the message and the tag, ignoring case', () => {
    expect(matches(line(1, 'info', 'Sending state 21.5', 'dallas'), 'DALLAS')).toBe(true);
    expect(matches(line(1, 'info', 'Sending state 21.5'), 'state 21')).toBe(true);
    expect(matches(line(1, 'info', 'Sending state'), 'wifi')).toBe(false);
    expect(matches(marker(1, 'restart', 'Restarted, 2 s offline'), 'restarted')).toBe(true);
    expect(matches(line(1, 'info', 'anything'), '  ')).toBe(true);
  });

  it('merges a reopened stream without duplicates and keeps the newest lines', () => {
    const cur = [line(1, 'info', 'a'), line(2, 'info', 'b')];
    const merged = mergeItems(cur, [line(2, 'info', 'b'), line(3, 'info', 'c'), marker(null, 'skipped', '4 lines skipped')], 3);
    expect(merged.map((i) => i.message)).toEqual(['b', 'c', '4 lines skipped']);
    expect(mergeItems(cur, [], 10)).toBe(cur);
  });

  it('writes the view as text with markers set apart', () => {
    expect(logText([line(1, 'warning', 'weak signal', 'wifi'), marker(2, 'offline', 'Device offline')])).toBe(
      '2026-01-02T03:04:05.006+00:00 [W][wifi] weak signal\n2026-01-02T03:04:06.000+00:00 ---- Device offline ----\n');
    expect(logText([])).toBe('');
    expect(levelLetter('config')).toBe('C');
  });
});
