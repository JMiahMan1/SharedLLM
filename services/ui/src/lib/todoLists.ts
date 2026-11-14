import { apiClient } from '../services/api';

/**
 * The Lists page: the hub's to-do lists (services/gateway/todo_routes.py over
 * homehub's `todo.*` entities). Every household member reads and writes them;
 * a list an admin locked is simply not in the answer for anyone off its
 * permit list. Nothing here knows a list's name: the lists are whatever the
 * hub has.
 */

export type ItemStatus = 'needs_action' | 'completed';

export interface TodoItem {
  uid: string;
  summary: string;
  status: ItemStatus;
  due: string | null;
  description: string | null;
}

export interface TodoFeatures {
  create: boolean;
  delete: boolean;
  update: boolean;
  move: boolean;
  due_date: boolean;
  due_datetime: boolean;
  description: boolean;
}

export interface TodoList {
  entity_id: string;
  name: string;
  open: number | null;
  available: boolean;
  features: TodoFeatures;
  items: TodoItem[];
}

const path = (entityId: string) => `/api/lists/${encodeURIComponent(entityId)}`;

export async function getLists(): Promise<TodoList[]> {
  return (await apiClient.get('/api/lists')).data?.lists ?? [];
}

export async function addItem(entityId: string, summary: string, extra: { due_date?: string | null } = {}): Promise<TodoList> {
  return (await apiClient.post(`${path(entityId)}/items`, { summary, ...extra })).data;
}

export async function updateItem(entityId: string, uid: string,
  change: { summary?: string; status?: ItemStatus; due_date?: string | null; description?: string | null }): Promise<TodoList> {
  return (await apiClient.patch(`${path(entityId)}/items/${encodeURIComponent(uid)}`, change)).data;
}

export async function removeItem(entityId: string, uid: string): Promise<TodoList> {
  return (await apiClient.delete(`${path(entityId)}/items/${encodeURIComponent(uid)}`)).data;
}

export async function moveItem(entityId: string, uid: string, previousUid: string | null): Promise<TodoList> {
  return (await apiClient.post(`${path(entityId)}/items/${encodeURIComponent(uid)}/move`, { previous_uid: previousUid })).data;
}

export async function clearCompleted(entityId: string): Promise<TodoList> {
  return (await apiClient.post(`${path(entityId)}/clear_completed`, {})).data;
}

/** Still to do, in the list's own order, then done. */
export function splitItems(items: TodoItem[]): { open: TodoItem[]; done: TodoItem[] } {
  return { open: items.filter((i) => i.status !== 'completed'), done: items.filter((i) => i.status === 'completed') };
}

/**
 * The `previous_uid` that moves the open item at `index` one step `dir`
 * among the open items (HA's move puts it right after that item; null is the
 * top). Returns undefined when it cannot move that way.
 *
 * The hub orders every item, done ones included, so "after the item above the
 * one above" is computed over the full list: moving up past the first open
 * item lands at the very top, moving down lands right after the next open one.
 */
export function previousForMove(items: TodoItem[], uid: string, dir: -1 | 1): string | null | undefined {
  const { open } = splitItems(items);
  const at = open.findIndex((i) => i.uid === uid);
  if (at < 0) return undefined;
  if (dir === -1) {
    if (at === 0) return undefined;
    const target = open[at - 1];
    const full = items.findIndex((i) => i.uid === target.uid);
    const before = items.slice(0, full).filter((i) => i.uid !== uid);
    return before.length ? before[before.length - 1].uid : null;
  }
  if (at === open.length - 1) return undefined;
  return open[at + 1].uid;
}

/** "Tue, Nov 18" or "Tue, Nov 18, 5:00 PM" for a due date, in the viewer's own words. */
export function dueText(due: string | null | undefined): string {
  if (!due) return '';
  if (/^\d{4}-\d{2}-\d{2}$/.test(due)) {
    const [y, m, d] = due.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  }
  const t = new Date(due);
  if (Number.isNaN(t.getTime())) return due;
  return t.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/** Whether a due date has passed (a date is due through the end of its day). */
export function isOverdue(due: string | null | undefined, now: Date = new Date()): boolean {
  if (!due) return false;
  if (/^\d{4}-\d{2}-\d{2}$/.test(due)) {
    const [y, m, d] = due.split('-').map(Number);
    return new Date(y, m - 1, d + 1).getTime() <= now.getTime();
  }
  const t = new Date(due).getTime();
  return Number.isFinite(t) && t < now.getTime();
}
