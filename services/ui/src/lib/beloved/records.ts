/**
 * Beloved's records: what each sealed record holds once opened on the device,
 * how a malformed or foreign one is ignored, and how the lot becomes one view.
 *
 * Every record is sealed with the private chat's key (`PrivateVault.sealRecord`)
 * before it leaves the phone; the server (services/gateway/beloved.py) keeps
 * the envelope, who wrote it and when, and nothing it could read. Inside, each
 * record names its own kind (`b`), so a record moved to another kind's folder
 * is not trusted.
 */
import { parseDay } from './dates';

export type BelovedKind = 'profile' | 'day' | 'note' | 'heart' | 'prayer' | 'devotion' | 'idea' | 'together' | 'plan';

/** The couple's shared settings: wedding and first-date days, birthdays, cover photo. */
export interface ProfileRecord {
  b: 'profile';
  wedding?: string;
  firstDate?: string;
  /** Birthdays by username. */
  birthdays?: Record<string, string>;
  /** The cover photo: an encrypted blob in the chat's blob store. */
  cover?: { blob: string; mime: string };
  /** What the couple calls themselves on the page ("Sam and Alex"), optional. */
  title?: string;
}

export interface DayRecord {
  b: 'day';
  name: string;
  date: string;
  /** Every year on that day (true) or just once (false). */
  yearly: boolean;
}

export interface NoteRecord {
  b: 'note';
  text: string;
}

export interface HeartRecord {
  b: 'heart';
}

export interface PrayerRecord {
  b: 'prayer';
  text: string;
  /** Who it is for (a username), when it is a request for one of them. */
  for?: string;
  /** Times each spouse marked "prayed for this", by username (seconds). */
  prayed?: Record<string, number[]>;
  /** When it was marked answered (seconds), and how. */
  answeredAt?: number;
  answer?: string;
}

export interface DevotionRecord {
  b: 'devotion';
  /** The day it is for (`YYYY-MM-DD`). */
  day: string;
  done: boolean;
  /** Your answer to the day's question, if you wrote one. */
  answer?: string;
}

export interface IdeaRecord {
  b: 'idea';
  /** A curated idea's id, or a custom one's title. */
  idea?: string;
  custom?: string;
  /** When it was suggested for (`YYYY-MM-DD`), if a day was chosen. */
  on?: string;
  doneAt?: number;
}

/** "Just us": a day the couple marked on their private calendar. */
export interface TogetherRecord {
  b: 'together';
  /** The calendar day (`YYYY-MM-DD`). */
  day: string;
  note?: string;
}

/** "Just us": an evening one of them planned for the two of them. */
export interface PlanRecord {
  b: 'plan';
  day: string;
  /** Optional local time, `HH:MM`. */
  time?: string;
  /** A suggestion's id (lib/beloved/justus.ts), if one was picked. */
  idea?: string;
  note?: string;
  /** When it was marked done (seconds); a done plan also marks its day. */
  doneAt?: number;
}

export type BelovedRecord =
  | ProfileRecord | DayRecord | NoteRecord | HeartRecord | PrayerRecord | DevotionRecord | IdeaRecord | TogetherRecord | PlanRecord;

/** What the server lists: one sealed record and who wrote it when. */
export interface SealedRecord {
  id: string;
  kind: BelovedKind;
  at: number;
  updated_at: number;
  by: string;
  own: boolean;
  by_name: string;
  sealed: string;
}

/** The couple as the server lists them: names and pictures, which the family already sees. */
export interface CoupleMember {
  username: string;
  display_name: string;
  avatar?: string | null;
  family_role?: string;
  me: boolean;
}

export interface BelovedOverview {
  me: string;
  couple: CoupleMember[];
  records: SealedRecord[];
}

export interface Opened<T extends BelovedRecord = BelovedRecord> {
  id: string;
  kind: BelovedKind;
  at: number;
  updatedAt: number;
  by: string;
  own: boolean;
  byName: string;
  record: T;
}

const MAX_TEXT = 4000;

function text(value: unknown, max = MAX_TEXT): string | undefined {
  return typeof value === 'string' && value.trim() ? value.slice(0, max) : undefined;
}

function day(value: unknown): string | undefined {
  return parseDay(value) ? (value as string) : undefined;
}

function clock(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  return match && Number(match[1]) < 24 && Number(match[2]) < 60 ? value : undefined;
}

function stamp(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : undefined;
}

function clean<T extends object>(out: T): T {
  for (const key of Object.keys(out) as Array<keyof T>) if (out[key] === undefined) delete out[key];
  return out;
}

/** A record of the expected kind with only well-shaped fields, or null. */
export function readRecord(kind: BelovedKind, raw: unknown): BelovedRecord | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (r.b !== kind) return null;
  switch (kind) {
    case 'profile': {
      const birthdays: Record<string, string> = {};
      if (r.birthdays && typeof r.birthdays === 'object') {
        for (const [who, when] of Object.entries(r.birthdays as Record<string, unknown>)) {
          const ok = day(when);
          if (ok && /^[A-Za-z0-9_.@-]{1,64}$/.test(who)) birthdays[who] = ok;
        }
      }
      const cover = r.cover as Record<string, unknown> | undefined;
      return clean<ProfileRecord>({
        b: 'profile',
        wedding: day(r.wedding),
        firstDate: day(r.firstDate),
        birthdays: Object.keys(birthdays).length ? birthdays : undefined,
        cover: cover && typeof cover.blob === 'string' && /^[0-9a-f]{32}$/.test(cover.blob) && typeof cover.mime === 'string'
          ? { blob: cover.blob, mime: cover.mime } : undefined,
        title: text(r.title, 120),
      });
    }
    case 'day': {
      const name = text(r.name, 120);
      const date = day(r.date);
      return name && date ? { b: 'day', name, date, yearly: r.yearly !== false } : null;
    }
    case 'note': {
      const body = text(r.text);
      return body ? { b: 'note', text: body } : null;
    }
    case 'heart':
      return { b: 'heart' };
    case 'prayer': {
      const body = text(r.text);
      if (!body) return null;
      const prayed: Record<string, number[]> = {};
      if (r.prayed && typeof r.prayed === 'object') {
        for (const [who, times] of Object.entries(r.prayed as Record<string, unknown>)) {
          if (Array.isArray(times)) {
            const ok = times.map(stamp).filter((t): t is number => t !== undefined).slice(-200);
            if (ok.length) prayed[who] = ok;
          }
        }
      }
      return clean<PrayerRecord>({
        b: 'prayer',
        text: body,
        for: text(r.for, 64),
        prayed: Object.keys(prayed).length ? prayed : undefined,
        answeredAt: stamp(r.answeredAt),
        answer: text(r.answer),
      });
    }
    case 'devotion': {
      const when = day(r.day);
      return when ? clean<DevotionRecord>({ b: 'devotion', day: when, done: r.done === true, answer: text(r.answer) }) : null;
    }
    case 'idea': {
      const idea = text(r.idea, 16);
      const custom = text(r.custom, 200);
      if (!idea && !custom) return null;
      return clean<IdeaRecord>({ b: 'idea', idea, custom, on: day(r.on), doneAt: stamp(r.doneAt) });
    }
    case 'together': {
      const when = day(r.day);
      return when ? clean<TogetherRecord>({ b: 'together', day: when, note: text(r.note, 1000) }) : null;
    }
    case 'plan': {
      const when = day(r.day);
      return when
        ? clean<PlanRecord>({ b: 'plan', day: when, time: clock(r.time), idea: text(r.idea, 16), note: text(r.note, 1000), doneAt: stamp(r.doneAt) })
        : null;
    }
    default:
      return null;
  }
}

export interface BelovedView {
  /** The newest profile record (both spouses edit the same one; the latest change wins). */
  profile?: Opened<ProfileRecord>;
  days: Opened<DayRecord>[];
  notes: Opened<NoteRecord>[];
  hearts: Opened<HeartRecord>[];
  prayers: Opened<PrayerRecord>[];
  devotions: Opened<DevotionRecord>[];
  ideas: Opened<IdeaRecord>[];
  /** "Just us": only ever shown on its own tab, never previewed anywhere else. */
  together: Opened<TogetherRecord>[];
  plans: Opened<PlanRecord>[];
}

/** Every opened record in one view, newest first within each list. */
export function buildView(opened: Opened[]): BelovedView {
  const newest = <T extends BelovedRecord>(kind: BelovedKind) =>
    opened.filter((o) => o.kind === kind).sort((a, b) => b.at - a.at || (a.id < b.id ? 1 : -1)) as Opened<T>[];
  const profiles = opened.filter((o) => o.kind === 'profile').sort((a, b) => b.updatedAt - a.updatedAt || (a.id < b.id ? 1 : -1));
  return {
    profile: profiles[0] as Opened<ProfileRecord> | undefined,
    days: newest<DayRecord>('day'),
    notes: newest<NoteRecord>('note'),
    hearts: newest<HeartRecord>('heart'),
    prayers: newest<PrayerRecord>('prayer'),
    devotions: newest<DevotionRecord>('devotion'),
    ideas: newest<IdeaRecord>('idea'),
    together: newest<TogetherRecord>('together'),
    plans: newest<PlanRecord>('plan'),
  };
}

/** A prayer with one more "prayed for this" from `who` at `now` (seconds). */
export function markPrayed(record: PrayerRecord, who: string, now: number): PrayerRecord {
  const prayed = { ...(record.prayed ?? {}) };
  prayed[who] = [...(prayed[who] ?? []), Math.floor(now)].slice(-200);
  return { ...record, prayed };
}

export function prayedCount(record: PrayerRecord): number {
  return Object.values(record.prayed ?? {}).reduce((sum, times) => sum + times.length, 0);
}

/**
 * What the bar's heart counts: a heart or a love note. Never the "Just us"
 * kinds: the heart is seen outside Beloved, so it must not hint at them.
 */
const LOVE_KINDS: ReadonlySet<BelovedKind> = new Set(['heart', 'note']);

type Stamped = Pick<SealedRecord, 'kind' | 'own' | 'at'>;

/**
 * Hearts and love notes from the other spouse newer than `seen` (the server's
 * seconds), for the heart on the private chat bar. Only the kind, the sender
 * and the time are read, which the server lists in the clear, so nothing is
 * decrypted to count them.
 */
export function unseenLove(records: readonly Stamped[], seen: number): number {
  return records.filter((r) => !r.own && LOVE_KINDS.has(r.kind) && r.at > seen).length;
}

/** The newest heart or note from the other spouse (server seconds), 0 when there is none. */
export function latestLove(records: readonly Stamped[]): number {
  return records.reduce((max, r) => (!r.own && LOVE_KINDS.has(r.kind) && r.at > max ? r.at : max), 0);
}
