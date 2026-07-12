// Per-calendar-account coloring, mirroring OpenSkyLight's model where an
// occurrence is colored by its assigned person, else its calendar. Events carry
// a `calendar` field (the source Google account, e.g. "kalebsummers85@gmail.com").
// That account can be assigned to a CalendarPerson (name + color); when it is,
// we color/label by the person so multiple accounts for the same user share a
// color. Unassigned accounts fall back to a stable hash of the raw id.

// Touch-friendly palette (OpenSkyLight's PERSON_COLORS / CALENDAR_COLORS)
export const CALENDAR_PALETTE = [
  '#E5484D', // red
  '#F76B15', // orange
  '#FFB224', // amber
  '#46A758', // green
  '#12A594', // teal
  '#0091FF', // blue
  '#6E56CF', // violet
  '#D6409F', // pink
  '#8E4EC6', // purple
  '#00749E', // deep cyan
];

export interface CalendarPerson {
  id: string;
  name: string;
  color: string;
  accounts: string[];
}

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function personFor(calendarId: string | undefined, people?: CalendarPerson[]): CalendarPerson | undefined {
  if (!calendarId || !people?.length) return undefined;
  return people.find((p) => p.accounts.includes(calendarId));
}

export const calendarColor = (calendarId?: string, people?: CalendarPerson[]): string => {
  const p = personFor(calendarId, people);
  if (p) return p.color;
  if (!calendarId) return '#a89f8d';
  return CALENDAR_PALETTE[hashString(calendarId) % CALENDAR_PALETTE.length];
};

export const calendarLabel = (calendarId?: string, people?: CalendarPerson[]): string => {
  const p = personFor(calendarId, people);
  if (p) return p.name;
  if (!calendarId) return 'Calendar';
  const email = calendarId.match(/^([^@]+)@/);
  if (email) {
    const local = email[1];
    if (calendarId.endsWith('@gmail.com') || calendarId.endsWith('@googlemail.com')) return local;
    if (calendarId.includes('@import.calendar.google.com')) return 'Imported';
    if (calendarId.includes('@group.calendar.google.com')) return 'Shared';
    return local;
  }
  return calendarId;
};
