/**
 * The device hub's hardware list for the Devices page (services/homehub/hardware.py), made up:
 * invented names, RFC 5737 addresses and 02:00:00 MACs. Used by homeMocks.ts.
 */

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();

const device = (over: Record<string, unknown>) => ({
  original_name: null, name_by_user: null, model_id: null, hw_version: null, serial_number: null,
  entry_state: 'loaded', entry_reason: null, configuration_url: null, entry_type: null, via_device_id: null,
  via_device_name: null, disabled_by: null, entities: 2, unavailable: 0, status: 'online', last_seen: ago(3),
  ...over,
});

export const HARDWARE = {
  areas: [{ area_id: 'kitchen', name: 'Kitchen' }, { area_id: 'living', name: 'Living room' }],
  devices: [
    device({ id: 'hw-lamp', name: 'Reading lamp with a rather long name for small phones', manufacturer: 'WiZ',
      model: 'ESP25_SHRGB_01', sw_version: '1.36.1', area_id: 'living', area: 'Living room', domain: 'wiz', label: 'WiZ',
      entry_id: 'e-wiz', entry_title: 'Reading lamp', abilities: ['color', 'color_temp', 'dim', 'power', 'sense'],
      connections: [{ type: 'ip', value: '192.0.2.21' }, { type: 'mac', value: '02:00:00:00:00:21' }] }),
    device({ id: 'hw-tv', name: 'Living TV', manufacturer: 'Example', model: 'Streamer', area_id: 'living',
      area: 'Living room', domain: 'roku', label: 'Roku', entry_id: 'e-roku', entry_title: 'Living TV',
      status: 'offline', unavailable: 2, abilities: ['launch_app', 'play_media', 'power'],
      connections: [{ type: 'ip', value: '192.0.2.66' }] }),
    device({ id: 'hw-plug', name: 'Kettle plug', manufacturer: 'Espressif', model: 'Plug', area_id: 'kitchen',
      area: 'Kitchen', domain: 'esphome', label: 'ESPHome', entry_id: 'e-esp', entry_title: 'Kettle plug',
      status: 'partial', unavailable: 1, abilities: ['power', 'sense'], connections: [{ type: 'mac', value: '02:00:00:00:00:31' }] }),
  ],
};

export const HARDWARE_DETAIL = {
  ...HARDWARE.devices[0], children: [], entry: null,
  entity_list: [
    { entity_id: 'light.reading_lamp', domain: 'light', platform: 'wiz', name: 'Reading lamp', name_by_user: null,
      original_name: null, entity_category: null, disabled_by: null, hidden_by: null, area_id: null, icon: null,
      capabilities: { abilities: ['color', 'dim', 'power'], services: ['light.turn_on', 'light.turn_off'], details: {}, protocol: {} },
      state: { entity_id: 'light.reading_lamp', domain: 'light', name: 'Reading lamp', state: 'on',
        attributes: { brightness: 180, supported_color_modes: ['rgb'] }, device_class: null, unit: null, icon: null,
        area_id: 'living', device_id: 'hw-lamp', last_changed: ago(10), abilities: ['color', 'dim', 'power'] } },
    { entity_id: 'sensor.reading_lamp_power', domain: 'sensor', platform: 'wiz', name: 'Reading lamp Power',
      name_by_user: null, original_name: 'Power', entity_category: 'diagnostic', disabled_by: null, hidden_by: null,
      area_id: null, icon: null, capabilities: null,
      state: { entity_id: 'sensor.reading_lamp_power', domain: 'sensor', name: 'Power', state: '4.2', attributes: {},
        device_class: 'power', unit: 'W', icon: null, area_id: 'living', device_id: 'hw-lamp', last_changed: ago(1) } },
  ],
};

export const MOVED_FINDING = {
  id: 'roku:x00000000001', integration: 'roku', label: 'Roku', name: 'Living TV', model: null, manufacturer: null,
  host: '192.0.2.96', mac: null, source: 'ssdp', first_seen: ago(600), last_seen: ago(2), known: true, entry_id: 'e-roku',
  entry_title: 'Living TV', ignored: false, new: false, adoptable: false, unsupported: null, needs: [],
  moved: { entry_id: 'e-roku', entry_title: 'Living TV', old_host: '192.0.2.66', new_host: '192.0.2.96' },
};

export const FLOWS = [
  { domain: 'roku', label: 'Roku', kind: 'local', fields: [{ key: 'host', label: 'Address', type: 'text' }] },
  { domain: 'roborock', label: 'Roborock account', kind: 'cloud', fields: [{ key: 'email', label: 'Email', type: 'text' }] },
  { domain: 'switch_as_x', label: 'Switch as another device', kind: 'helper', fields: [
    { key: 'entity_id', label: 'Switch', type: 'select', options: [{ value: 'switch.kettle', label: 'Kettle plug' }] },
    { key: 'target_domain', label: 'Show it as', type: 'select', options: [{ value: 'light', label: 'Light' }] }] },
];
