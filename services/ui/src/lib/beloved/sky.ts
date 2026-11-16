/**
 * The night sky for a planned evening: how cloudy the forecast says the night
 * will be (`night_cloud`, the mean cloud cover from 9 PM to 2 AM, from
 * services/common/weather.py) and how bright the moon is.
 *
 * The moon is worked out here, not fetched: its phase follows the mean
 * synodic month from a known new moon, which is within about half a day of
 * the true phase, plenty to say "nearly full" a month ahead. The clouds only
 * exist for the days the forecast covers (about a week).
 */
import { dayNumber, type Ymd } from './dates';

const SYNODIC_DAYS = 29.530588853;
/** A new moon: 2000-01-06 18:14 UTC, as days since 1970-01-01. */
const NEW_MOON = 10_962 + (18 * 60 + 14) / 1440;
/** The moon is judged at about 10 PM. */
const EVENING = 22 / 24;

export interface Moon {
  /** 0 new, 0.5 full, back to 1. */
  phase: number;
  /** Share of the disc lit, 0 to 1. */
  lit: number;
  name: string;
}

const NAMES: Array<[number, string]> = [
  [0.034, 'New moon'],
  [0.216, 'Waxing crescent'],
  [0.284, 'First quarter'],
  [0.466, 'Waxing gibbous'],
  [0.534, 'Full moon'],
  [0.716, 'Waning gibbous'],
  [0.784, 'Last quarter'],
  [0.966, 'Waning crescent'],
  [1, 'New moon'],
];

export function moonOn(day: Ymd): Moon {
  const age = (dayNumber(day) + EVENING - NEW_MOON) / SYNODIC_DAYS;
  const phase = age - Math.floor(age);
  const lit = (1 - Math.cos(2 * Math.PI * phase)) / 2;
  const name = NAMES.find(([upTo]) => phase < upTo)?.[1] ?? 'New moon';
  return { phase, lit, name };
}

export type SkyLook = 'clear' | 'bright-moon' | 'some-cloud' | 'cloudy';

export interface NightSky {
  /** Mean cloud cover over the night, %; null beyond the forecast. */
  cloud: number | null;
  moon: Moon;
  /** How the night looks for the stars; null without a cloud figure. */
  look: SkyLook | null;
  /** "Clear skies, good for stars", or null without a cloud figure. */
  verdict: string | null;
}

/** Above this much cloud the stars are mostly hidden; at or below the clear mark they are out. */
const CLEAR_AT = 25;
const CLOUDY_ABOVE = 60;
/** A moon this full washes out the faint stars. */
const BRIGHT_MOON = 0.6;

const VERDICTS: Record<SkyLook, string> = {
  clear: 'Clear skies, good for stars',
  'bright-moon': 'Clear, but a bright moon hides the faint stars',
  'some-cloud': 'Some clouds, stars in the gaps',
  cloudy: 'Cloudy, the stars will be hidden',
};

export function nightSky(day: Ymd, nightCloud: number | null | undefined): NightSky {
  const moon = moonOn(day);
  const cloud = typeof nightCloud === 'number' && Number.isFinite(nightCloud) ? Math.round(nightCloud) : null;
  let look: SkyLook | null = null;
  if (cloud !== null) {
    if (cloud > CLOUDY_ABOVE) look = 'cloudy';
    else if (cloud > CLEAR_AT) look = 'some-cloud';
    else look = moon.lit >= BRIGHT_MOON ? 'bright-moon' : 'clear';
  }
  return { cloud, moon, look, verdict: look ? VERDICTS[look] : null };
}

/** An evening plan (no time, or from 5 PM on) or a stargazing one gets the night sky. */
export function wantsNightSky(time: string | undefined, idea: string | undefined): boolean {
  if (idea === 'stars') return true;
  if (!time) return true;
  const hour = Number(time.split(':')[0]);
  return !Number.isFinite(hour) || hour >= 17 || hour < 4;
}
