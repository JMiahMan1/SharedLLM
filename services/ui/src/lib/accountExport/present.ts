/**
 * How an opened export reads: every kind of record turned into what the app
 * shows (a weight with its date, a night's sleep, a highlight with its verse,
 * a trip with its places and distance, a conversation as bubbles), in the
 * reader's locale, never internal ids, keys, raw flags or field names.
 *
 * presentExport is one self-contained function on purpose: the encrypted page
 * (page.ts) embeds its source text, so the page and the app show the same
 * thing. It must not use anything from outside its own body except the
 * browser's built-ins (Intl, Date, JSON, Math). The unit tests run it from its
 * source text to keep that true.
 */

export interface PresentedTurn {
  who: 'me' | 'jarvis';
  text: string;
  at?: string;
}

export interface PresentedItem {
  title: string;
  subtitle?: string;
  detail?: string;
  /** A colour to show beside the item (a highlight's colour). */
  swatch?: string;
  /** Labelled facts, already humanised. */
  facts?: { label: string; value: string }[];
  transcript?: PresentedTurn[];
}

export interface PresentedGroup {
  /** "January 2026", or empty when the list is not split by month. */
  label: string;
  count: number;
  items: PresentedItem[];
}

export interface PresentedCollection {
  name: string;
  label: string;
  count: number;
  /** For reading only (never restored). */
  readOnly: boolean;
  /** False when no formatter knows this kind and it is shown as tidy cards. */
  formatted: boolean;
  groups: PresentedGroup[];
}

export interface PresentedService {
  service: string;
  label: string;
  total: number;
  collections: PresentedCollection[];
}

export interface PresentOptions {
  locale?: string;
  timeZone?: string;
}

type AnyRow = Record<string, unknown>;

export function presentExport(
  services: Record<string, Record<string, AnyRow[]>>,
  options?: PresentOptions,
): PresentedService[] {
  const opts = options || {};
  const locale = opts.locale || (typeof navigator !== 'undefined' ? navigator.language : 'en-US') || 'en-US';
  const tz = opts.timeZone;

  // --- reading values -------------------------------------------------------
  const str = (v: unknown): string => (v === null || v === undefined ? '' : String(v)).trim();
  const num = (v: unknown): number | null => {
    if (v === null || v === undefined || v === '') return null;
    const n = typeof v === 'number' ? v : Number(v);
    return Number.isFinite(n) ? n : null;
  };
  const json = (v: unknown): unknown => {
    if (typeof v !== 'string') return v;
    const t = v.trim();
    if (!t || (t[0] !== '{' && t[0] !== '[')) return v;
    try {
      return JSON.parse(t);
    } catch {
      return v;
    }
  };
  const obj = (v: unknown): AnyRow => {
    const p = json(v);
    return p && typeof p === 'object' && !Array.isArray(p) ? (p as AnyRow) : {};
  };
  const arr = (v: unknown): unknown[] => {
    const p = json(v);
    return Array.isArray(p) ? p : [];
  };
  /** A time from ISO text, a YYYY-MM-DD day (local), or epoch seconds or milliseconds. */
  const when = (v: unknown): Date | null => {
    if (v === null || v === undefined || v === '') return null;
    if (typeof v === 'number' || (typeof v === 'string' && /^\d{9,13}(\.\d+)?$/.test(v))) {
      const n = Number(v);
      const d = new Date(n < 1e11 ? n * 1000 : n);
      return Number.isNaN(d.getTime()) ? null : d;
    }
    const s = String(v);
    const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
    if (day) return new Date(Number(day[1]), Number(day[2]) - 1, Number(day[3]), 12);
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : d;
  };
  const isDayOnly = (v: unknown): boolean => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v);
  const fmt = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(locale, tz ? { ...o, timeZone: tz } : o);
  const dayF = fmt({ day: 'numeric', month: 'short', year: 'numeric' });
  const dayLongF = fmt({ weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  const timeF = fmt({ hour: 'numeric', minute: '2-digit' });
  const monthF = fmt({ month: 'long', year: 'numeric' });
  const date = (v: unknown): string => {
    const d = when(v);
    return d ? dayF.format(d) : '';
  };
  const dateTime = (v: unknown): string => {
    const d = when(v);
    if (!d) return '';
    return isDayOnly(v) ? dayF.format(d) : `${dayF.format(d)}, ${timeF.format(d)}`;
  };
  const clock = (v: unknown): string => {
    const d = when(v);
    if (d && !isDayOnly(v) && !/^\d{1,2}:\d{2}/.test(String(v))) return timeF.format(d);
    const m = /^(\d{1,2}):(\d{2})/.exec(String(v || ''));
    if (!m) return '';
    return timeF.format(new Date(2000, 0, 1, Number(m[1]), Number(m[2])));
  };
  const nf = (n: number, digits = 1) => new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(n);
  const count = (n: number, one: string, many: string) => `${nf(n, 0)} ${n === 1 ? one : many}`;
  const duration = (minutes: number | null): string => {
    if (minutes === null || minutes < 0) return '';
    const h = Math.floor(minutes / 60);
    const m = Math.round(minutes % 60);
    if (!h) return `${m} min`;
    return m ? `${h} h ${m} min` : `${h} h`;
  };
  const human = (key: string): string => {
    const s = key.replace(/_json$|_ml$|_mg$|_g$|_c$|_minutes$|_seconds$|_ms$/, '').replace(/[_-]+/g, ' ').trim();
    return s ? s[0].toUpperCase() + s.slice(1) : '';
  };
  const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

  // --- units: the health profile's choice, else the locale's -------------------
  const region = (() => {
    try {
      return new Intl.Locale(locale).maximize().region || '';
    } catch {
      return '';
    }
  })();
  const healthProfile = ((services.health || {}).profile || [])[0] || {};
  const imperial = healthProfile.units ? healthProfile.units === 'imperial' : ['US', 'LR', 'MM', 'GB'].includes(region);
  const miles = (mi: number | null): string => {
    if (mi === null) return '';
    return imperial ? `${nf(mi)} mi` : `${nf(mi * 1.609344)} km`;
  };

  // --- shared pieces ---------------------------------------------------------
  const BOOKS: Record<string, string> = {
    Gen: 'Genesis', Exod: 'Exodus', Lev: 'Leviticus', Num: 'Numbers', Deut: 'Deuteronomy', Josh: 'Joshua', Judg: 'Judges',
    Ruth: 'Ruth', '1Sam': '1 Samuel', '2Sam': '2 Samuel', '1Kgs': '1 Kings', '2Kgs': '2 Kings', '1Chr': '1 Chronicles',
    '2Chr': '2 Chronicles', Ezra: 'Ezra', Neh: 'Nehemiah', Esth: 'Esther', Job: 'Job', Ps: 'Psalms', Prov: 'Proverbs',
    Eccl: 'Ecclesiastes', Song: 'Song of Songs', Isa: 'Isaiah', Jer: 'Jeremiah', Lam: 'Lamentations', Ezek: 'Ezekiel',
    Dan: 'Daniel', Hos: 'Hosea', Joel: 'Joel', Amos: 'Amos', Obad: 'Obadiah', Jonah: 'Jonah', Mic: 'Micah', Nah: 'Nahum',
    Hab: 'Habakkuk', Zeph: 'Zephaniah', Hag: 'Haggai', Zech: 'Zechariah', Mal: 'Malachi', Matt: 'Matthew', Mark: 'Mark',
    Luke: 'Luke', John: 'John', Acts: 'Acts', Rom: 'Romans', '1Cor': '1 Corinthians', '2Cor': '2 Corinthians',
    Gal: 'Galatians', Eph: 'Ephesians', Phil: 'Philippians', Col: 'Colossians', '1Thess': '1 Thessalonians',
    '2Thess': '2 Thessalonians', '1Tim': '1 Timothy', '2Tim': '2 Timothy', Titus: 'Titus', Phlm: 'Philemon', Heb: 'Hebrews',
    Jas: 'James', '1Pet': '1 Peter', '2Pet': '2 Peter', '1John': '1 John', '2John': '2 John', '3John': '3 John',
    Jude: 'Jude', Rev: 'Revelation',
  };
  const verseRef = (r: AnyRow): string => {
    const book = BOOKS[str(r.osis)] || str(r.osis);
    if (!book) return str(r.ref);
    const ch = num(r.chapter);
    const a = num(r.verse_start);
    const b = num(r.verse_end);
    let out = ch !== null ? `${book} ${ch}` : book;
    if (a !== null) out += `:${a}${b !== null && b !== a ? `-${b}` : ''}`;
    return out;
  };
  const COLORS: Record<string, string> = { yellow: '#facc15', gold: '#eab308', green: '#4ade80', blue: '#60a5fa', pink: '#f472b6', purple: '#a78bfa', orange: '#fb923c', red: '#f87171' };
  const place = (loc: unknown): string => {
    const l = obj(loc);
    return str(l.name) || str(l.address) || '';
  };
  const words = (v: unknown): string => str(v).replace(/[_-]+/g, ' ');
  const sentence = (v: unknown): string => cap(words(v));
  const listOf = (v: unknown): string => {
    const a = arr(v).map((x) => (typeof x === 'object' && x ? str((x as AnyRow).name || (x as AnyRow).title || (x as AnyRow).label) : words(x))).filter(Boolean);
    return a.join(', ');
  };
  /** Text of one chat message's parts (text only; tools and thinking are named, not dumped). */
  const partsText = (v: unknown): string => {
    const parts = arr(v);
    if (!parts.length) return typeof v === 'string' && !v.trim().startsWith('[') ? v.trim() : '';
    const out: string[] = [];
    let tools = 0;
    for (const p of parts) {
      const part = (p && typeof p === 'object' ? p : {}) as AnyRow;
      if (part.type === 'text' && str(part.text)) out.push(str(part.text));
      else if (part.type === 'tool' || part.type === 'mission') tools += 1;
    }
    if (tools) out.push(tools === 1 ? '(used a tool)' : `(used ${tools} tools)`);
    return out.join('\n\n');
  };

  // --- the tidy card for anything without its own formatter --------------------
  const HIDDEN = /^(id|.*_id|username|user_id|owner|author|target|chat_id|session_id|external_id|source|seed|version_code|set_id|counted|answers|metadata_json|stages_json|data|mime|original|original_mime|crop|note_path|updated_by|decided_by|actor_kind)$/;
  const factValue = (key: string, v: unknown): string => {
    if (v === null || v === undefined || v === '') return '';
    if (typeof v === 'boolean') return v ? 'Yes' : 'No';
    if (typeof v === 'object') {
      if ('$base64' in (v as AnyRow)) return '';
      return Array.isArray(v) ? listOf(v) : '';
    }
    if (/(_at|_on|day|date|time)$/.test(key) && when(v)) return dateTime(v);
    const parsed = json(v);
    if (parsed !== v) return Array.isArray(parsed) ? listOf(parsed) : '';
    return String(v);
  };
  const facts = (r: AnyRow, skip?: string[]): { label: string; value: string }[] =>
    Object.keys(r)
      .filter((k) => !HIDDEN.test(k) && !(skip || []).includes(k))
      .map((k) => ({ label: human(k), value: factValue(k, r[k]) }))
      .filter((f) => f.label && f.value);
  const card = (title: string, r: AnyRow, skip?: string[]): PresentedItem => ({ title, facts: facts(r, skip) });
  /** A settings blob (theme, weather, notifications...) as labelled facts. */
  const settingsFacts = (v: unknown): { label: string; value: string }[] => {
    const o = obj(v);
    return Object.keys(o)
      .map((k) => {
        const x = o[k];
        let value = '';
        if (typeof x === 'boolean') value = x ? 'On' : 'Off';
        else if (typeof x === 'number' || typeof x === 'string') value = /(_at|_on|until)$/.test(k) && when(x) ? dateTime(x) : words(x);
        else if (Array.isArray(x)) value = listOf(x);
        else if (x && typeof x === 'object') value = str((x as AnyRow).name) || Object.keys(x).map(human).join(', ');
        return { label: human(k), value };
      })
      .filter((f) => f.label && f.value);
  };

  /** The book catalogue choices on the profile (the Google Books key is never in the file). */
  const BOOK_CATALOGUES: Record<string, string> = { google_books: 'Google Books', open_library: 'Open Library' };
  const bookFacts = (v: unknown): { label: string; value: string }[] => {
    const b = obj(v);
    const onOff = Object.keys(BOOK_CATALOGUES)
      .filter((k) => typeof b[`${k}_enabled`] === 'boolean')
      .map((k) => `${BOOK_CATALOGUES[k]} ${b[`${k}_enabled`] ? 'on' : 'off'}`);
    return [
      { label: 'Book catalogues', value: onOff.join(', ') },
      { label: 'Book catalogue order', value: arr(b.order).map((k) => BOOK_CATALOGUES[str(k)] || words(k)).join(', then ') },
      { label: 'Open Library contact', value: str(b.open_library_email) },
    ];
  };

  // --- one formatter per kind: returns the item and the time it sorts by --------
  type Out = { item: PresentedItem; at: Date | null } | null;
  type Ctx = { all: Record<string, AnyRow[]> };
  const METRICS: Record<string, string> = {
    weight: 'Weight', body_fat: 'Body fat', resting_hr: 'Resting heart rate', hrv: 'Heart rate variability', spo2: 'Blood oxygen',
    water_ml: 'Water', water: 'Water', steps: 'Steps', blood_pressure_systolic: 'Blood pressure (upper)',
    blood_pressure_diastolic: 'Blood pressure (lower)', glucose: 'Blood sugar', temperature: 'Temperature',
  };
  const UNITS: Record<string, string> = { bpm: 'bpm', kg: 'kg', lb: 'lb', ms: 'ms', '%': '%', percent: '%', ml: 'ml', mmhg: 'mmHg', 'mg/dl': 'mg/dL' };
  const metricValue = (r: AnyRow): string => {
    const v = num(r.value);
    if (v === null) return '';
    const unit = str(r.unit).toLowerCase();
    if (unit === 'kg' && imperial && r.metric === 'weight') return `${nf(v * 2.20462)} lb`;
    if (unit === 'ml' && imperial) return `${nf(v / 29.5735)} fl oz`;
    const u = UNITS[unit] ?? str(r.unit);
    return u === '%' ? `${nf(v)}%` : `${nf(v)}${u ? ` ${u}` : ''}`;
  };
  const transcriptOf = (turns: unknown[]): PresentedTurn[] =>
    turns
      .map((t) => {
        const turn = (t && typeof t === 'object' ? t : {}) as AnyRow;
        const text = str(turn.text || turn.content);
        return { who: turn.role === 'user' ? 'me' : 'jarvis', text } as PresentedTurn;
      })
      .filter((t) => t.text);

  const F: Record<string, Record<string, (r: AnyRow, c: Ctx) => Out>> = {
    identity: {
      profile: (r) => ({
        at: null,
        item: {
          title: str(r.display_name) || str(r.username),
          subtitle: [sentence(r.family_role) === 'Other' ? '' : sentence(r.family_role), r.birth_date ? `Born ${date(r.birth_date)}` : ''].filter(Boolean).join(' · '),
          facts: [
            { label: 'User name', value: str(r.username) },
            { label: 'Voice Jarvis uses', value: words(r.preferred_tts_voice) },
            { label: 'Nextcloud', value: [str(r.nextcloud_user), str(r.nextcloud_url)].filter(Boolean).join(' at ') },
            { label: 'Skylight', value: str(r.skylight_email) },
            { label: 'Mail', value: str(r.mail_user) },
            { label: 'Audiobookshelf', value: str(r.audiobookshelf_user) },
            { label: 'GitHub', value: str(r.github_user) },
            { label: 'GitLab', value: str(r.gitlab_user) },
            { label: 'Git', value: str(r.git_user) },
            ...bookFacts(r.book_metadata),
          ].filter((f) => f.value),
        },
      }),
      avatar_image: (r) => ({ at: when(r.updated_at), item: { title: 'Your profile picture', subtitle: r.updated_at ? `Set ${date(r.updated_at)}` : '' } }),
      theme: (r) => ({ at: null, item: { title: 'Theme', facts: settingsFacts(r.data) } }),
      weather: (r) => ({ at: null, item: { title: 'Weather', facts: settingsFacts(r.data) } }),
      calendar_settings: (r) => ({ at: null, item: { title: 'Calendar', facts: settingsFacts(r.data) } }),
      notification_preferences: (r) => {
        const o = obj(r.data);
        const off = Object.keys(o).filter((k) => o[k] === false).map(words);
        return { at: null, item: { title: 'Notifications', detail: off.length ? `Turned off: ${off.join(', ')}` : 'Every kind of notice is on' } };
      },
      activity_sharing: (r) => ({ at: null, item: { title: 'Activity sharing', facts: settingsFacts(r.data) } }),
      profile_sharing: (r) => {
        const areas = arr(obj(r.data).areas).map(sentence).filter(Boolean);
        return {
          at: null,
          item: {
            title: 'Profile sharing',
            detail: areas.length ? `The family may see: ${areas.join(', ')}` : 'Private: the family sees none of these areas',
          },
        };
      },
      dashboard_widgets: (r) => ({
        at: null,
        item: {
          title: sentence(r.widget_key),
          subtitle: [r.visibility === 'hidden' ? 'Hidden' : 'Shown', sentence(r.size), r.is_pinned ? 'Pinned' : ''].filter(Boolean).join(' · '),
        },
      }),
      workspace_sessions: (r) => {
        const d = obj(r.data);
        const tabs = arr(d.tabs).length;
        return {
          at: when(r.updated_at),
          item: { title: str(d.currentPath) || 'Workspace', subtitle: [tabs ? count(tabs, 'open tab', 'open tabs') : '', r.updated_at ? `Last used ${dateTime(r.updated_at)}` : ''].filter(Boolean).join(' · ') },
        };
      },
      workspace_chats: (r, c) => {
        const msgs = (c.all.workspace_chat_messages || []).filter((m) => m.chat_id === r.id);
        msgs.sort((a, b) => str(a.created_at).localeCompare(str(b.created_at)));
        return {
          at: when(r.updated_at || r.created_at),
          item: {
            title: str(r.title) || 'Workspace chat',
            subtitle: [dateTime(r.updated_at || r.created_at), count(msgs.length, 'message', 'messages')].filter(Boolean).join(' · '),
            transcript: msgs
              .map((m) => ({ who: m.role === 'user' ? 'me' : 'jarvis', text: partsText(m.parts), at: dateTime(m.created_at) }) as PresentedTurn)
              .filter((t) => t.text),
          },
        };
      },
      // Shown inside their chats.
      workspace_chat_messages: () => null,
      assistant_conversations: (r) => {
        const turns = transcriptOf(arr(r.turns));
        const d = when(r.at);
        return {
          at: d,
          item: { title: d ? `${dayLongF.format(d)}, ${timeF.format(d)}` : 'Conversation with Jarvis', subtitle: count(turns.length, 'message', 'messages'), transcript: turns },
        };
      },
      feature_usage: (r) => ({
        at: when(r.last_used_at),
        item: { title: sentence(r.feature), subtitle: [num(r.uses) !== null ? count(num(r.uses) as number, 'use', 'uses') : '', r.last_used_at ? `last ${date(r.last_used_at)}` : ''].filter(Boolean).join(' · ') },
      }),
      screen_rules: (r) => ({ at: when(r.updated_at), item: { title: 'Screen time rules', subtitle: r.updated_at ? `Set ${date(r.updated_at)}` : '', facts: settingsFacts(r.data) } }),
      recommendation_lanes: (r) => ({ at: when(r.updated_at), item: { title: 'Recommendations', facts: settingsFacts(r.data) } }),
      screen_time_requests: (r) => ({
        at: when(r.created_at || r.day),
        item: {
          title: `Asked for ${count(num(r.minutes) || 0, 'minute', 'minutes')} more`,
          subtitle: [date(r.day), sentence(r.status), num(r.granted_minutes) ? `${num(r.granted_minutes)} granted` : ''].filter(Boolean).join(' · '),
          detail: str(r.note),
        },
      }),
      guard_requests: (r) => ({
        at: when(r.created_at),
        item: { title: str(r.label) || sentence(r.target) || 'Request', subtitle: [sentence(r.kind), sentence(r.status), dateTime(r.created_at)].filter(Boolean).join(' · '), detail: str(r.note) },
      }),
      unlock_requests: (r) => ({
        at: when(r.created_at),
        item: {
          title: `Asked to unlock for ${count(num(r.minutes_asked) || 0, 'minute', 'minutes')}`,
          subtitle: [sentence(r.device_kind), sentence(r.status), num(r.minutes_granted) ? `${num(r.minutes_granted)} granted` : '', dateTime(r.created_at)].filter(Boolean).join(' · '),
          detail: str(r.note),
        },
      }),
      chore_checks: (r) => ({ at: when(r.day || r.created_at), item: { title: str(r.title) || 'Chore', subtitle: [date(r.day), sentence(r.status)].filter(Boolean).join(' · ') } }),
      guardian_notes_written: (r) => ({ at: when(r.created_at), item: { title: `About ${str(r.child)}`, subtitle: [dateTime(r.created_at), r.shared ? 'Shared with the other parent' : ''].filter(Boolean).join(' · '), detail: str(r.text) } }),
      account_history: (r) => {
        const changed = arr(r.changes).map((x) => words((x as AnyRow).field)).filter(Boolean);
        return {
          at: when(r.at),
          item: { title: sentence(str(r.action).replace(/\./g, ' ')), subtitle: [dateTime(r.at), r.actor && r.actor !== r.target ? `by ${str(r.actor)}` : ''].filter(Boolean).join(' · '), detail: changed.length ? `Changed: ${changed.join(', ')}` : str(r.note) },
        };
      },
    },
    health: {
      profile: (r) => {
        const h = num(r.height_cm);
        const height = h === null ? '' : imperial ? `${Math.floor(h / 2.54 / 12)} ft ${Math.round((h / 2.54) % 12)} in` : `${nf(h, 0)} cm`;
        return {
          at: null,
          item: {
            title: 'Health profile',
            facts: [
              { label: 'Born', value: str(r.birth_year) },
              { label: 'Height', value: height },
              { label: 'Activity', value: sentence(r.activity_level) },
              { label: 'Goal', value: sentence(r.goal) },
              { label: 'Sleep need', value: duration(num(r.sleep_need_minutes)) },
              { label: 'Water goal', value: num(r.water_goal_ml) !== null ? metricValue({ value: r.water_goal_ml, unit: 'ml' }) : '' },
              { label: 'Weekly active time', value: duration(num(r.weekly_active_minutes)) },
              { label: 'Units', value: sentence(r.units) },
            ].filter((f) => f.value),
          },
        };
      },
      preferences: () => ({ at: null, item: { title: 'Health page preferences', detail: 'Which measurements your Health page shows.' } }),
      metrics: (r) => ({ at: when(r.recorded_at), item: { title: `${METRICS[str(r.metric)] || sentence(r.metric)} ${metricValue(r)}`.trim(), subtitle: dateTime(r.recorded_at) } }),
      sleep: (r) => {
        const span = [clock(r.bedtime), clock(r.wake_time)].filter(Boolean).join(' to ');
        const eff = num(r.efficiency);
        return {
          at: when(r.night),
          item: {
            title: `${duration(num(r.duration_minutes)) || 'Sleep'}${r.night ? `, night of ${date(r.night)}` : ''}`,
            subtitle: [span, eff !== null ? `${nf(eff > 1 ? eff : eff * 100, 0)}% efficient` : '', r.quality ? `${sentence(r.quality)} quality` : ''].filter(Boolean).join(' · '),
            detail: str(r.note),
          },
        };
      },
      checkins: (r) => {
        const scores = (['mood', 'energy', 'stress', 'soreness'] as const)
          .filter((k) => num(r[k]) !== null)
          .map((k) => `${cap(k)} ${num(r[k])}`);
        return { at: when(r.day), item: { title: `Check-in, ${date(r.day)}`, subtitle: scores.join(' · '), detail: str(r.note) } };
      },
      food: (r) => {
        const kcal = num(r.calories);
        const servings = num(r.servings);
        return {
          at: when(r.day),
          item: {
            title: str(r.name) || 'Food',
            subtitle: [sentence(r.meal), date(r.day), kcal !== null ? `${nf((kcal || 0) * (servings || 1), 0)} kcal` : '', servings && servings !== 1 ? `${nf(servings)} servings` : ''].filter(Boolean).join(' · '),
            detail: [num(r.protein_g) !== null ? `Protein ${nf(num(r.protein_g) as number)} g` : '', num(r.carbs_g) !== null ? `Carbs ${nf(num(r.carbs_g) as number)} g` : '', num(r.fat_g) !== null ? `Fat ${nf(num(r.fat_g) as number)} g` : ''].filter(Boolean).join(' · '),
          },
        };
      },
      fasting: (r) => {
        const start = when(r.started_at);
        const end = when(r.ended_at);
        const minutes = start && end ? (end.getTime() - start.getTime()) / 60000 : null;
        return {
          at: start,
          item: {
            title: `${sentence(r.kind) || 'Fast'}${minutes !== null ? `, ${duration(minutes)}` : r.ended_at ? '' : ' (not finished)'}`,
            subtitle: [dateTime(r.started_at), num(r.target_hours) ? `goal ${num(r.target_hours)} h` : ''].filter(Boolean).join(' · '),
            detail: str(r.intention),
          },
        };
      },
      goals: (r) => ({ at: when(r.week_start), item: { title: `${sentence(r.kind)}: ${count(num(r.target_days) || 0, 'day', 'days')} a week`, subtitle: `Week of ${date(r.week_start)}` } }),
      cycle_settings: (r) => ({
        at: null,
        item: {
          title: 'Cycle tracking',
          subtitle: r.enabled ? 'On' : 'Off',
          facts: [
            { label: 'Usual cycle', value: num(r.average_cycle_length) ? count(num(r.average_cycle_length) as number, 'day', 'days') : '' },
            { label: 'Usual period', value: num(r.average_period_length) ? count(num(r.average_period_length) as number, 'day', 'days') : '' },
          ].filter((f) => f.value),
        },
      }),
      cycle_periods: (r) => ({ at: when(r.start_day), item: { title: `Period from ${date(r.start_day)}${r.end_day ? ` to ${date(r.end_day)}` : ''}`, subtitle: r.flow ? `${sentence(r.flow)} flow` : '', detail: str(r.note) } }),
      cycle_logs: (r) => ({
        at: when(r.day),
        item: { title: `Cycle log, ${date(r.day)}`, subtitle: [listOf(r.symptoms_json), r.mood ? `Mood ${words(r.mood)}` : '', r.energy ? `Energy ${words(r.energy)}` : ''].filter(Boolean).join(' · '), detail: str(r.note) },
      }),
      recipe_ratings: (r) => {
        const stars = Math.max(0, Math.min(5, num(r.stars) || 0));
        return { at: when(r.updated_at), item: { title: str(r.recipe).split('/').pop()?.replace(/\.[a-z]+$/i, '') || 'Recipe', subtitle: `${'★'.repeat(stars)}${'☆'.repeat(5 - stars)}` } };
      },
      recipes_made: (r) => ({ at: when(r.day), item: { title: str(r.recipe).split('/').pop()?.replace(/\.[a-z]+$/i, '') || 'Recipe', subtitle: `Made ${date(r.day)}` } }),
      shares_given: (r) => ({ at: when(r.created_at), item: { title: `Shared with ${str(r.viewer)}`, subtitle: [sentence(r.scope), r.created_at ? `since ${date(r.created_at)}` : ''].filter(Boolean).join(' · ') } }),
      cheers_sent: (r) => ({ at: when(r.day), item: { title: `You cheered ${str(r.to_user)}`, subtitle: [sentence(r.kind), date(r.day)].filter(Boolean).join(' · ') } }),
      cheers_received: (r) => ({ at: when(r.day), item: { title: `${cap(str(r.from_user))} cheered you`, subtitle: [sentence(r.kind), date(r.day)].filter(Boolean).join(' · ') } }),
      challenges_joined: (r) => ({ at: when(r.joined_at), item: { title: 'Family challenge', subtitle: r.joined_at ? `Joined ${date(r.joined_at)}` : '' } }),
    },
    bible: {
      reading_state: (r) => ({
        at: when(r.last_read_at),
        item: {
          title: `Reading ${BOOKS[str(r.last_book)] || str(r.last_book)} ${str(r.last_chapter)}`.trim(),
          subtitle: r.last_read_at ? `Last read ${dateTime(r.last_read_at)}` : '',
          facts: [
            { label: 'Translation', value: str(r.default_version).toUpperCase() },
            { label: 'Reminder', value: clock(r.reminder_time) },
            { label: 'Layout', value: sentence(r.layout) },
          ].filter((f) => f.value),
        },
      }),
      marks: (r) => ({
        at: when(r.created_at),
        item: {
          title: verseRef(r),
          subtitle: [r.kind === 'bookmark' ? 'Bookmark' : `${cap(words(r.color)) || 'Highlight'}${r.kind === 'highlight' || !r.kind ? ' highlight' : ''}`, str(r.version_code).toUpperCase(), date(r.created_at)].filter(Boolean).join(' · '),
          detail: str(r.note_preview),
          swatch: r.kind === 'bookmark' ? undefined : COLORS[str(r.color).toLowerCase()] || (str(r.color).startsWith('#') ? str(r.color) : undefined),
        },
      }),
      memory_verses: (r) => ({
        at: when(r.created_at),
        item: { title: str(r.ref) || verseRef(r), subtitle: [`Level ${num(r.level) || 0} of 5`, r.due_on ? `next review ${date(r.due_on)}` : ''].filter(Boolean).join(' · ') },
      }),
      plan_enrollments: (r) => {
        const done = str(r.completed_days).split(',').filter(Boolean).length;
        return {
          at: when(r.started_on),
          item: { title: sentence(r.plan_id), subtitle: [`Started ${date(r.started_on)}`, count(done, 'day read', 'days read'), r.finished_on ? `finished ${date(r.finished_on)}` : r.active === false ? 'stopped' : ''].filter(Boolean).join(' · ') },
        };
      },
      plan_reflections: (r) => ({ at: when(r.updated_at), item: { title: `${sentence(r.plan_id)}, day ${str(r.day)}`, subtitle: date(r.updated_at), detail: str(r.text) } }),
      reading_events: (r) => ({ at: when(r.day), item: { title: str(r.ref) ? `${sentence(r.kind)}: ${words(r.ref)}` : sentence(r.kind), subtitle: date(r.day) } }),
      achievements: (r) => ({ at: when(r.earned_on), item: { title: sentence(r.achievement_id), subtitle: [`Earned ${date(r.earned_on)}`, num(r.points) ? count(num(r.points) as number, 'point', 'points') : ''].filter(Boolean).join(' · ') } }),
      game_scores: (r) => ({
        at: when(r.created_at || r.day),
        item: { title: `${sentence(r.mode)}: ${str(r.correct)} of ${str(r.total)}`, subtitle: [date(r.day), num(r.duration_ms) ? duration((num(r.duration_ms) as number) / 60000).replace(/^0 min$/, 'under a minute') : ''].filter(Boolean).join(' · ') },
      }),
      study_sessions: (r, c) => {
        const turns = (c.all.study_session_turns || []).filter((t) => t.session_id === r.id);
        turns.sort((a, b) => str(a.created_at).localeCompare(str(b.created_at)));
        const transcript: PresentedTurn[] = [];
        for (const t of turns) {
          if (str(t.question)) transcript.push({ who: 'me', text: str(t.question), at: dateTime(t.created_at) });
          if (str(t.answer)) transcript.push({ who: 'jarvis', text: str(t.answer) });
        }
        return { at: when(r.updated_at || r.created_at), item: { title: str(r.title) || str(r.reference) || 'Bible study', subtitle: [str(r.reference) !== str(r.title) ? str(r.reference) : '', dateTime(r.updated_at || r.created_at)].filter(Boolean).join(' · '), transcript } };
      },
      study_session_turns: () => null,
      plan_groups_joined: (r) => ({ at: when(r.joined_at || r.created_at), item: { title: 'Reading plan group', subtitle: [sentence(r.status), r.joined_at ? `joined ${date(r.joined_at)}` : ''].filter(Boolean).join(' · ') } }),
    },
    geo: {
      places: (r) => ({ at: when(r.created_at), item: { title: str(r.name) || 'Saved place', subtitle: [r.scope === 'family' ? 'Shared with the family' : 'Just you', num(r.radius) ? `${nf(num(r.radius) as number, 0)} m around it` : '', r.important ? 'Important' : ''].filter(Boolean).join(' · ') } }),
      place_reminders: (r) => ({ at: when(r.created_at), item: { title: str(r.title) || 'Reminder', subtitle: `When you ${r.on === 'leave' ? 'leave' : 'arrive at'} ${str(r.zone_name) || 'a place'}` } }),
      trips: (r) => {
        const from = place(r.start_location);
        const to = place(r.end_location);
        const start = when(r.start_time);
        const end = when(r.end_time);
        return {
          at: start,
          item: {
            title: from && to ? (from === to ? `Around ${from}` : `${from} to ${to}`) : from || to || 'Trip',
            subtitle: [start ? `${dayLongF.format(start)}, ${timeF.format(start)}${end ? ` to ${timeF.format(end)}` : ''}` : '', miles(num(r.distance_miles)), duration(num(r.duration_seconds) !== null ? (num(r.duration_seconds) as number) / 60 : null)].filter(Boolean).join(' · '),
            detail: [str(r.vehicle_name), sentence(r.activity_type) !== 'Driving' ? sentence(r.activity_type) : ''].filter(Boolean).join(' · '),
          },
        };
      },
      workouts: (r) => {
        const start = when(r.start_time);
        const steps = num(r.steps);
        return {
          at: start,
          item: {
            title: `${sentence(r.activity_type) || 'Workout'}${num(r.duration_seconds) ? `, ${duration((num(r.duration_seconds) as number) / 60)}` : ''}`,
            subtitle: [start ? `${dayLongF.format(start)}, ${timeF.format(start)}` : '', miles(num(r.distance_miles)), steps ? count(steps, 'step', 'steps') : ''].filter(Boolean).join(' · '),
            detail: r.auto_detected ? 'Found from your location' : str(r.notes),
          },
        };
      },
      daily_steps: (r) => ({ at: when(r.day), item: { title: count(num(r.steps) || 0, 'step', 'steps'), subtitle: dayLongF.format(when(r.day) || new Date(0)) } }),
      goals: (r) => {
        const v = num(r.value);
        const name = r.goal === 'daily_steps_goal' || r.goal === 'daily_steps' ? 'Daily steps' : sentence(r.goal);
        return { at: null, item: { title: name, subtitle: v !== null ? nf(v, 0) : str(r.value) } };
      },
      achievements: (r) => ({ at: when(r.earned_on), item: { title: sentence(r.achievement_id), subtitle: r.earned_on ? `Earned ${date(r.earned_on)}` : '' } }),
      visits: (r) => {
        const stays = arr(r.stays);
        return { at: null, item: { title: 'A place you stopped', subtitle: stays.length ? count(stays.length, 'visit', 'visits') : '' } };
      },
      location_history: (r) => ({ at: when(r.t), item: { title: dateTime(r.t), subtitle: num(r.lat) !== null ? `${nf(num(r.lat) as number, 4)}, ${nf(num(r.lon) as number, 4)}` : '' } }),
    },
  };

  const SERVICE_LABELS: Record<string, string> = { identity: 'Profile and settings', health: 'Health', bible: 'Bible', geo: 'Places, trips and steps' };
  const LABELS: Record<string, string> = {
    profile: 'Profile', avatar_image: 'Profile picture', theme: 'Theme', weather: 'Weather', calendar_settings: 'Calendar',
    notification_preferences: 'Notifications', activity_sharing: 'Activity sharing', profile_sharing: 'Profile sharing', dashboard_widgets: 'Dashboard',
    workspace_sessions: 'Workspace sessions', workspace_chats: 'Workspace chats', assistant_conversations: 'Conversations with Jarvis',
    feature_usage: 'Features you use', screen_rules: 'Screen time rules', recommendation_lanes: 'Recommendations',
    screen_time_requests: 'Screen time requests', guard_requests: 'App requests', unlock_requests: 'Unlock requests', chore_checks: 'Chores',
    guardian_notes_written: 'Journal notes you wrote', account_history: 'Account history', preferences: 'Preferences',
    metrics: 'Measurements', sleep: 'Sleep', checkins: 'Check-ins', food: 'Food log', fasting: 'Fasting', goals: 'Goals',
    cycle_settings: 'Cycle settings', cycle_periods: 'Periods', cycle_logs: 'Cycle log', recipe_ratings: 'Recipe ratings',
    recipes_made: 'Recipes made', shares_given: 'Shared with', cheers_sent: 'Cheers sent', cheers_received: 'Cheers received',
    challenges_joined: 'Challenges', reading_state: 'Reading', marks: 'Highlights and bookmarks', memory_verses: 'Memory verses',
    plan_enrollments: 'Reading plans', plan_reflections: 'Plan reflections', reading_events: 'Reading history',
    achievements: 'Achievements', game_scores: 'Game scores', study_sessions: 'Bible studies', plan_groups_joined: 'Plan groups',
    places: 'Saved places', place_reminders: 'Place reminders', trips: 'Trips', workouts: 'Workouts', daily_steps: 'Daily steps',
    visits: 'Places you stopped', location_history: 'Location trail',
  };
  const READ_ONLY = ['feature_usage', 'screen_rules', 'recommendation_lanes', 'screen_time_requests', 'guard_requests', 'unlock_requests', 'chore_checks', 'guardian_notes_written', 'account_history', 'shares_given', 'cheers_sent', 'cheers_received', 'challenges_joined', 'plan_groups_joined', 'visits', 'location_history'];

  const out: PresentedService[] = [];
  for (const service of Object.keys(services)) {
    const all = services[service] || {};
    const collections: PresentedCollection[] = [];
    for (const name of Object.keys(all)) {
      const rows = all[name] || [];
      const formatter = (F[service] || {})[name];
      const made: { item: PresentedItem; at: Date | null }[] = [];
      for (const row of rows) {
        let result: Out;
        try {
          result = formatter ? formatter(row, { all }) : { item: card(human(name), row), at: when(row.created_at || row.updated_at || row.day) };
        } catch {
          result = { item: card(human(name), row), at: null };
        }
        if (result && (result.item.title || (result.item.facts && result.item.facts.length))) made.push(result);
      }
      if (!made.length) continue;
      const dated = made.filter((m) => m.at).length;
      made.sort((a, b) => (b.at ? b.at.getTime() : 0) - (a.at ? a.at.getTime() : 0));
      const groups: PresentedGroup[] = [];
      if (made.length > 8 && dated >= made.length / 2) {
        for (const m of made) {
          const label = m.at ? monthF.format(m.at) : 'No date';
          let g = groups[groups.length - 1];
          if (!g || g.label !== label) {
            g = { label, count: 0, items: [] };
            groups.push(g);
          }
          g.items.push(m.item);
          g.count += 1;
        }
      } else {
        groups.push({ label: '', count: made.length, items: made.map((m) => m.item) });
      }
      collections.push({ name, label: LABELS[name] || human(name), count: made.length, readOnly: READ_ONLY.includes(name), formatted: Boolean(formatter), groups });
    }
    if (collections.length) {
      out.push({ service, label: SERVICE_LABELS[service] || human(service), total: collections.reduce((n, c) => n + c.count, 0), collections });
    }
  }
  return out;
}
