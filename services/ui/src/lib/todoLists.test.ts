import { describe, expect, it } from 'vitest';
import { dueText, isOverdue, previousForMove, splitItems, type TodoItem } from './todoLists';

const item = (uid: string, status: TodoItem['status'] = 'needs_action'): TodoItem => ({
  uid, summary: uid.toUpperCase(), status, due: null, description: null,
});

describe('moving an item', () => {
  // The hub orders done items too: a, x(done), b, c.
  const items = [item('a'), item('x', 'completed'), item('b'), item('c')];

  it('moves up to right after the item above the one above, or to the top', () => {
    expect(previousForMove(items, 'b', -1)).toBeNull();
    expect(previousForMove(items, 'c', -1)).toBe('x');
  });

  it('moves down to right after the next open item', () => {
    expect(previousForMove(items, 'a', 1)).toBe('b');
    expect(previousForMove(items, 'b', 1)).toBe('c');
  });

  it('cannot move past either end or move a done item', () => {
    expect(previousForMove(items, 'a', -1)).toBeUndefined();
    expect(previousForMove(items, 'c', 1)).toBeUndefined();
    expect(previousForMove(items, 'x', 1)).toBeUndefined();
  });

  it('splits open from done, keeping the order', () => {
    expect(splitItems(items).open.map((i) => i.uid)).toEqual(['a', 'b', 'c']);
    expect(splitItems(items).done.map((i) => i.uid)).toEqual(['x']);
  });
});

describe('due dates', () => {
  const now = new Date(2026, 10, 14, 12, 0);

  it('a date is due through the end of its day', () => {
    expect(isOverdue('2026-11-14', now)).toBe(false);
    expect(isOverdue('2026-11-13', now)).toBe(true);
    expect(isOverdue(null, now)).toBe(false);
  });

  it('a time is overdue once it passes', () => {
    expect(isOverdue(new Date(2026, 10, 14, 11, 0).toISOString(), now)).toBe(true);
    expect(isOverdue(new Date(2026, 10, 14, 13, 0).toISOString(), now)).toBe(false);
  });

  it('reads a date as the local calendar day, not UTC midnight', () => {
    expect(dueText('2026-11-14')).toContain('14');
    expect(dueText('')).toBe('');
    expect(dueText('not a date')).toBe('not a date');
  });
});
