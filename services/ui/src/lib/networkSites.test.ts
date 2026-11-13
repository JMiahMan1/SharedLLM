import { describe, expect, it } from 'vitest';
import { groupBySite, lastSeen, siteOf } from './networkSites';

describe('sites by device', () => {
  it('names the site a lookup belongs to', () => {
    expect(siteOf('i.scdn.co')).toBe('scdn.co');
    expect(siteOf('audio-ak.spotifycdn.com.')).toBe('spotifycdn.com');
    expect(siteOf('www.bbc.co.uk')).toBe('bbc.co.uk');
    expect(siteOf('example.com')).toBe('example.com');
    expect(siteOf('Localhost')).toBe('localhost');
    expect(siteOf('192.0.2.7')).toBe('192.0.2.7');
  });

  it('groups a device’s lookups by site, most first', () => {
    const groups = groupBySite([
      { domain: 'a.example.com', lookups: 3, last: 10 },
      { domain: 'b.example.com', lookups: 5, last: 20 },
      { domain: 'other.example.org', lookups: 4, last: 5 },
    ]);
    expect(groups.map((g) => [g.site, g.lookups])).toEqual([['example.com', 8], ['example.org', 4]]);
    expect(groups[0].domains.map((d) => d.domain)).toEqual(['b.example.com', 'a.example.com']);
    expect(groups[0].last).toBe(20);
  });

  it('says when, in words', () => {
    const now = 1_000_000_000_000;
    expect(lastSeen(now / 1000 - 30, now)).toBe('just now');
    expect(lastSeen(now / 1000 - 600, now)).toBe('10 min ago');
    expect(lastSeen(now / 1000 - 7200, now)).toBe('2 h ago');
    expect(lastSeen(now / 1000 - 86_400, now)).toBe('1 day ago');
  });
});
