/**
 * Made-up entities for the button, number, select, time, remote, update, image and vacuum-room controls.
 * Invented names only; used by e2e/home/entity-controls.spec.ts on top of homeMocks.ts.
 */

const at = (min: number) => new Date(Date.now() - min * 60_000).toISOString();

const ent = (entity_id: string, name: string, state: string, extra: Record<string, unknown> = {}) => ({
  entity_id, domain: entity_id.split('.')[0], name, state, attributes: {}, device_class: null, unit: null, icon: null,
  area_id: 'utility', device_id: null, last_changed: at(30), ...extra,
});

export const CONTROLS_ROOMS = {
  floors: [],
  areas: [
    { area_id: 'utility', name: 'Utility room with a rather long name for small phones', floor_id: null, icon: null, entities: [
      ent('button.utility_chime', 'Door chime', 'unknown', { abilities: ['press'] }),
      ent('number.utility_fan_level', 'Fan level', '40', { unit: '%', attributes: { min: 0, max: 100, step: 10, mode: 'slider',
        unit_of_measurement: '%' }, abilities: ['set_value'] }),
      ent('select.utility_washer_program', 'Washer program', 'cotton', {
        attributes: { options: ['cotton', 'delicates', 'quick_wash_with_a_long_option_name'] }, abilities: ['select_option'] }),
      ent('time.utility_quiet_start', 'Quiet hours start', '22:00:00', { abilities: ['set_time'] }),
      ent('remote.utility_tv', 'Utility TV', 'on', { attributes: { command_list: ['back', 'down', 'home', 'left', 'right',
        'select', 'up', 'volume_up'], activity_list: ['Weather', 'Music'], current_activity: 'Weather' },
      abilities: ['remote_command', 'power', 'activity'] }),
      ent('remote.utility_old_box', 'Old box', 'on', { abilities: ['remote_command', 'power'] }),
      ent('vacuum.utility_robot', 'Robot', 'docked', { attributes: { battery_level: 100, segments: [
        { id: 16, name: 'Kitchen' }, { id: 17, name: 'Hallway' }, { id: 18, name: 'A room with a very long name indeed' }] },
      abilities: ['vacuum_start', 'vacuum_return', 'pause', 'clean_area', 'remote_command'] }),
      ent('image.utility_robot_map', 'Robot map', at(2), { abilities: ['image'] }),
    ] },
  ],
  unassigned: [],
};

const entity = (entity_id: string, name: string, entity_category: string | null, state: Record<string, unknown>,
  abilities: string[]) => ({
  entity_id, domain: entity_id.split('.')[0], platform: 'esphome', name, name_by_user: null, original_name: null,
  entity_category, disabled_by: null, hidden_by: null, area_id: null, icon: null,
  capabilities: { abilities, services: [], details: {}, protocol: {} },
  state: { entity_id, domain: entity_id.split('.')[0], name, attributes: {}, device_class: null, unit: null, icon: null,
    area_id: 'living', device_id: 'hw-lamp', last_changed: at(5), abilities, ...state },
});

/** A device with a firmware update the hub can install, one it cannot (no OTA), and a configuration number. */
export function controlsDetail(base: Record<string, unknown>, skipped: string | null = null) {
  return {
    ...base, children: [], entry: null,
    entity_list: [
      entity('update.porch_node_firmware', 'Porch node Firmware', 'config', {
        state: 'on', attributes: { installed_version: '2026.9.0', latest_version: '2026.10.2', supported_features: 1,
          release_url: 'https://example.com/changelog/', title: 'ESPHome', in_progress: false, skipped_version: null } },
      ['firmware', 'update']),
      entity('update.porch_node_bulb_firmware', 'Bulb Firmware', 'config', {
        // As the hub answers after update.skip: the skipped version is kept and the update is off.
        state: skipped === '1.1.0' ? 'off' : 'on', attributes: { installed_version: '1.0.0', latest_version: '1.1.0',
          supported_features: 0, skipped_version: skipped, release_summary: 'Fixes a long-standing flicker on very dim levels.' } },
      ['firmware']),
      entity('number.porch_node_led_brightness', 'LED brightness', 'config', {
        state: '0.4', unit: null, attributes: { min: 0, max: 1, step: 0.1, mode: 'box' } }, ['set_value']),
      entity('button.porch_node_restart', 'Restart', 'config', { state: 'unknown' }, ['press']),
    ],
  };
}
