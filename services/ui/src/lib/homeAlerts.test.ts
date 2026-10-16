import { describe, expect, it } from 'vitest';
import { cleanHomeAlert, parseHomeAlerts, removeHomeAlert, upsertHomeAlert } from './homeAlerts';

describe('homeAlerts', () => {
  it('parses the setting and drops anything malformed', () => {
    const raw = JSON.stringify([
      { entity_id: 'lock.back', event: 'door_unlocked_night', people: ['Michele'], minutes: '10' },
      { entity_id: 'nodot', event: 'doorbell' },
      { entity_id: 'binary_sensor.x', event: 'bogus' },
      'junk',
    ]);
    expect(parseHomeAlerts(raw)).toEqual([
      { entity_id: 'lock.back', event: 'door_unlocked_night', people: ['michele'], minutes: 10 },
    ]);
    expect(parseHomeAlerts('not json')).toEqual([]);
    expect(parseHomeAlerts(undefined)).toEqual([]);
    expect(parseHomeAlerts('{"a": 1}')).toEqual([]);
  });

  it('keeps only the tuning an event uses, and no blanks', () => {
    expect(cleanHomeAlert({ entity_id: ' event.ding ', event: 'doorbell', people: ['', 'A', 'a'], minutes: 5, watts: 3 }))
      .toEqual({ entity_id: 'event.ding', event: 'doorbell', people: ['a'] });
    expect(cleanHomeAlert({ entity_id: 'sensor.dryer', event: 'appliance_done', people: [], minutes: 0, watts: 40 }))
      .toEqual({ entity_id: 'sensor.dryer', event: 'appliance_done', people: [], watts: 40 });
  });

  it('replaces the same entity and event, and keeps other events on that entity', () => {
    let list = upsertHomeAlert([], { entity_id: 'binary_sensor.door', event: 'doorbell', people: [] });
    list = upsertHomeAlert(list, { entity_id: 'binary_sensor.door', event: 'motion_away', people: [] });
    list = upsertHomeAlert(list, { entity_id: 'binary_sensor.door', event: 'doorbell', people: ['bob'] });
    expect(list).toHaveLength(2);
    expect(list.find((a) => a.event === 'doorbell')?.people).toEqual(['bob']);
    expect(removeHomeAlert(list, { entity_id: 'binary_sensor.door', event: 'doorbell' })).toHaveLength(1);
  });
});
