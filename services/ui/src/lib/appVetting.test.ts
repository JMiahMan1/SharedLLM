import { describe, expect, it } from 'vitest';
import {
  ageFrom, factChips, inventoryFingerprint, lookupWords, profileLines, ratingLine, shouldSendInventory, sinceWords,
  sizeWords, unusedLine, worstLevel, type InstalledAppRow,
} from './appVetting';
import { CHAT_GAME, CHECKING, verdict } from '../test/appVettingFixtures';

const DAY = 86_400_000;
const NOW = 1_800_000_000_000;

describe('app vetting words', () => {
  it('picks the most serious level', () => {
    expect(worstLevel(['medium', 'severe', 'high'])).toBe('severe');
    expect(worstLevel([null, undefined])).toBeNull();
  });

  it('says where a lookup stands, and nothing once the verdict is in', () => {
    expect(lookupWords(CHECKING)).toMatch(/Checking/);
    expect(lookupWords(verdict({ lookup: 'not_on_play' }))).toMatch(/Not on Google Play/);
    expect(lookupWords(verdict({ review_state: 'no_model' }))).toMatch(/No local model/);
    expect(lookupWords(verdict({ lookup: 'error', lookup_reason: 'the page has no app data' }))).toContain('the page has no app data');
    expect(lookupWords(CHAT_GAME)).toBeNull();
  });

  it('lists the facts a parent weighs, without repeating purchases', () => {
    expect(factChips(CHAT_GAME)).toEqual(['Teen', 'In-app purchases', 'Users Interact']);
    expect(factChips(verdict({ store: 'Galaxy Store', rating: null }))).toEqual(['Galaxy Store']);
  });

  it('compares the rating with the limit', () => {
    expect(ratingLine({ rating: 'Teen', limit: 'Everyone 10+', over_age: true })).toBe('Rated Teen · above the Everyone 10+ limit');
    expect(ratingLine({ rating: 'Everyone', limit: 'Teen', over_age: false })).toBe('Rated Everyone · within the Teen limit');
    expect(ratingLine({ rating: 'Teen', limit: null, over_age: null })).toMatch(/no age limit/);
    expect(ratingLine({ rating: null, limit: 'Teen', over_age: null })).toBeNull();
  });

  it('says how long ago and how big', () => {
    expect(sinceWords(NOW - 3 * DAY, NOW)).toBe('3 days ago');
    expect(sinceWords(NOW - 150 * DAY, NOW)).toBe('5 months ago');
    expect(sinceWords(null, NOW)).toBe('never');
    expect(sizeWords(314_572_800)).toBe('300 MB');
    expect(sizeWords(3 * 1024 ** 3)).toBe('3.0 GB');
    expect(sizeWords(null)).toBe('');
    expect(unusedLine({ package: 'a.b', label: 'B', last_used: NOW - 40 * DAY, first_install: null, size_bytes: null,
      days_unused: 40, never_used: false, devices: [] }, NOW)).toBe('Last used 40 days ago');
    expect(unusedLine({ package: 'a.b', label: 'B', last_used: null, first_install: NOW - 90 * DAY, size_bytes: 2_000_000,
      days_unused: 90, never_used: true, devices: [] }, NOW)).toBe('No use recorded since it was installed 3 months ago · 2 MB');
  });

  it('works an age out of a birth date', () => {
    expect(ageFrom('2016-11-15', new Date(2026, 10, 15))).toBe(10);
    expect(ageFrom('2016-11-16', new Date(2026, 10, 15))).toBe(9);
    expect(ageFrom('not a date')).toBeNull();
  });
});

describe('when the device sends its app list', () => {
  const app = (pkg: string, extra: Partial<InstalledAppRow> = {}): InstalledAppRow => ({
    package: pkg, label: pkg, installer: 'com.android.vending', first_install: 1, last_update: 2, launchable: true,
    permissions: [], last_used: 3, size_bytes: 4, ...extra,
  });

  it('ignores last use and size, which move all the time', () => {
    expect(inventoryFingerprint([app('a.b', { last_used: 9, size_bytes: 99 })])).toBe(inventoryFingerprint([app('a.b')]));
    expect(inventoryFingerprint([app('a.b', { permissions: ['camera'] })])).not.toBe(inventoryFingerprint([app('a.b')]));
    expect(inventoryFingerprint([app('a.b'), app('c.d')])).toBe(inventoryFingerprint([app('c.d'), app('a.b')]));
  });

  it('sends at once when special access is switched on or an app file is newly fingerprinted', () => {
    expect(inventoryFingerprint([app('a.b', { special_access: ['accessibility'] })]))
      .not.toBe(inventoryFingerprint([app('a.b')]));
    expect(inventoryFingerprint([app('a.b', { apk_sha256: 'ab'.repeat(32) })])).not.toBe(inventoryFingerprint([app('a.b')]));
  });

  it('sends on a change, once a day, or for another person', () => {
    const last = { fingerprint: 'x', at: NOW, user: 'kid' };
    expect(shouldSendInventory(null, 'x', 'kid', NOW)).toBe(true);
    expect(shouldSendInventory(last, 'x', 'kid', NOW + 60_000)).toBe(false);
    expect(shouldSendInventory(last, 'y', 'kid', NOW + 60_000)).toBe(true);
    expect(shouldSendInventory(last, 'x', 'kid', NOW + DAY)).toBe(true);
    expect(shouldSendInventory(last, 'x', 'sib', NOW + 60_000)).toBe(true);
  });
});

describe('the outside checks in words', () => {
  it('says what MalwareBazaar found, and nothing per app when the key is missing (the card says it once)', async () => {
    const { malwareWords } = await import('./appVetting');
    expect(malwareWords({ state: 'malware', reason: null, signature: 'SpyNote' })).toBe('Known malware on abuse.ch MalwareBazaar (SpyNote)');
    expect(malwareWords({ state: 'not_known', reason: null })).toBe('Not a known malware file (abuse.ch MalwareBazaar)');
    expect(malwareWords({ state: 'not_set_up', reason: 'x' })).toBeNull();
    expect(malwareWords({ state: 'no_hash', reason: null })).toMatch(/latest Jarvis update/);
    expect(malwareWords(null)).toBeNull();
  });

  it('counts the reviews read by kind', async () => {
    const { reviewsReadWords } = await import('./appVetting');
    expect(reviewsReadWords({ relevant: 20, newest: 15, lowest: 12, state: 'done', errors: {} }))
      .toBe('Read 47 reviews: 20 most relevant, 15 newest, 12 one and two star');
    expect(reviewsReadWords({ relevant: 1, newest: 0, lowest: 0, state: 'reading', errors: {} }))
      .toBe('Read 1 review: 1 most relevant; reading more');
    expect(reviewsReadWords({})).toBeNull();
  });

  it("shows Common Sense's age as a chip only when it reviewed the app", async () => {
    const { commonSenseChip } = await import('./appVetting');
    expect(commonSenseChip({ status: 'found', reason: null, age: 13, age_label: 'age 13+' })).toBe('Common Sense age 13+');
    expect(commonSenseChip({ status: 'not_found', reason: null })).toBeNull();
    expect(commonSenseChip(null)).toBeNull();
  });
});

describe('what the phone says about an app it is asking about', () => {
  it('puts the version, where it came from, the size, what it can reach and who signed it', () => {
    const lines = profileLines({
      version_name: '4.2.1', version_code: 4210, installer: 'com.android.vending', size_bytes: 12 * 1024 * 1024,
      permissions: ['camera'], capabilities: ['overlay'], special_access: [], cert_sha1: ['AB'.repeat(20)],
      apk_sha256: 'a'.repeat(64), apk_hash_state: 'ok',
    });
    expect(lines).toEqual([
      'Version 4.2.1 (4210)',
      'Installed by Google Play',
      '12 MB',
      'Can draw over other apps, use the camera',
      'Signed by ABABABABABAB',
      'Its file was read and checked against known bad apps',
    ]);
  });

  it('says a sideload is a sideload, and an unread file is unread', () => {
    const lines = profileLines({ installer: null, apk_hash_state: 'too_large' });
    expect(lines).toEqual(['Installed directly, not from a store (sideloaded)', 'Its file was not read yet']);
  });

  it('shows nothing when the phone sent nothing, and never invents a value', () => {
    expect(profileLines(undefined)).toEqual([]);
    expect(profileLines({})).toEqual([]);
  });
});
