import { describe, it, expect } from 'vitest';
import {
  abilityLabel, ago, filterDevices, groupDevices, headlineAbilities, integrationsIn, sortIntegrations,
  type HardwareDevice, type IntegrationRow,
} from './hardware';

const dev = (over: Partial<HardwareDevice>): HardwareDevice => ({
  id: 'd', name: 'Device', original_name: null, name_by_user: null, manufacturer: null, model: null,
  sw_version: null, hw_version: null, serial_number: null, area_id: null, area: null, domain: null, label: null,
  entry_id: null, entry_title: null, entry_state: 'loaded', entry_reason: null, connections: [],
  configuration_url: null, via_device_id: null, via_device_name: null, disabled_by: null, entities: 1,
  unavailable: 0, status: 'online', last_seen: null, abilities: [], ...over,
});

const lamp = dev({ id: 'l', name: 'Porch lamp', manufacturer: 'WiZ', area_id: 'porch', area: 'Porch', domain: 'wiz',
  label: 'WiZ', abilities: ['dim', 'power', 'sense'], connections: [{ type: 'ip', value: '192.0.2.21' }] });
const tv = dev({ id: 't', name: 'Den TV', area_id: 'den', area: 'Den', domain: 'roku', label: 'Roku', status: 'offline',
  abilities: ['play_media', 'launch_app'] });
const plug = dev({ id: 'p', name: 'Spare plug', domain: 'esphome', label: 'ESPHome', status: 'partial' });

describe('hardware list helpers', () => {
  it('searches names, makers, rooms and addresses, and filters by chip', () => {
    expect(filterDevices([lamp, tv, plug], { query: 'wiz' }).map((d) => d.id)).toEqual(['l']);
    expect(filterDevices([lamp, tv, plug], { query: '192.0.2.21' }).map((d) => d.id)).toEqual(['l']);
    expect(filterDevices([lamp, tv, plug], { query: 'den' }).map((d) => d.id)).toEqual(['t']);
    expect(filterDevices([lamp, tv, plug], { integration: 'esphome' }).map((d) => d.id)).toEqual(['p']);
    expect(filterDevices([lamp, tv, plug], { status: 'offline' }).map((d) => d.id)).toEqual(['t']);
    expect(filterDevices([lamp, tv, plug], { ability: 'play_media' }).map((d) => d.id)).toEqual(['t']);
  });

  it('groups by room with no room last, by integration, and by status worst first', () => {
    expect(groupDevices([plug, lamp, tv], 'area').map((g) => g.label)).toEqual(['Den', 'Porch', 'No area']);
    expect(groupDevices([plug, lamp, tv], 'integration').map((g) => g.label)).toEqual(['ESPHome', 'Roku', 'WiZ']);
    expect(groupDevices([lamp, plug, tv], 'status').map((g) => g.key)).toEqual(['offline', 'partial', 'online']);
    expect(groupDevices([lamp], 'none')[0].devices).toEqual([lamp]);
  });

  it('counts integrations, words abilities and times', () => {
    expect(integrationsIn([lamp, tv, plug, dev({ id: 'x', domain: 'wiz', label: 'WiZ' })]))
      .toEqual([{ domain: 'esphome', label: 'ESPHome', count: 1 }, { domain: 'roku', label: 'Roku', count: 1 },
        { domain: 'wiz', label: 'WiZ', count: 2 }]);
    expect(abilityLabel('play_media')).toBe('Play media');
    expect(headlineAbilities(['sense', 'dim', 'toggle', 'power'])).toEqual(['dim', 'power']);
    const now = Date.parse('2026-11-12T12:00:00Z');
    expect(ago('2026-11-12T11:59:30Z', now)).toBe('just now');
    expect(ago('2026-11-12T11:30:00Z', now)).toBe('30 min ago');
    expect(ago('2026-11-12T06:00:00Z', now)).toBe('6 h ago');
    expect(ago('2026-11-09T12:00:00Z', now)).toBe('3 d ago');
    expect(ago(null, now)).toBe('');
  });

  it('puts integrations that need attention first and the ones not set up last', () => {
    const row = (domain: string, status: string): IntegrationRow => ({
      domain, label: domain, has_driver: true, status, entries: [], devices: 0, entities: 0, unavailable: 0,
      discovery: [], manual: true, self_added: null, syncs: false, new_found: 0, moved: 0,
    });
    expect(sortIntegrations([row('a', 'not_set_up'), row('b', 'loaded'), row('c', 'setup_retry')]).map((r) => r.domain))
      .toEqual(['c', 'b', 'a']);
  });
});
