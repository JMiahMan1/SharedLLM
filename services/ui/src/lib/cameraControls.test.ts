import { describe, expect, it } from 'vitest';
import { actionService, doorIsOpen, shortName, statusAlert, statusText } from './cameraControls';

const s = (domain: string, state: string, device_class: string | null = null, unit: string | null = null) =>
  ({ domain, state, device_class, unit, role: domain });

describe('camera controls', () => {
  it('drops the camera’s own name from a control’s name', () => {
    expect(shortName('Backyard Camera 1 Person', 'Backyard Camera 1')).toBe('Person');
    expect(shortName('Garage Door', 'Garage Camera')).toBe('Garage Door');
    expect(shortName('Garage Siren', 'Garage Camera')).toBe('Garage Siren');
    expect(shortName('Porch Cam Light', 'Porch Cam')).toBe('Light');
    expect(shortName('Garage Camera', 'Garage Camera')).toBe('Garage Camera');
    expect(shortName('Siren', 'Yard')).toBe('Siren');
  });

  it('says what each status means in words', () => {
    expect(statusText(s('binary_sensor', 'on', 'motion'))).toBe('Detected');
    expect(statusText(s('binary_sensor', 'off'))).toBe('Clear');
    expect(statusText(s('binary_sensor', 'on', 'door'))).toBe('Open');
    expect(statusText(s('binary_sensor', 'off', 'garage_door'))).toBe('Closed');
    expect(statusText(s('sensor', '64', 'battery', '%'))).toBe('64%');
    expect(statusText(s('sensor', '72.46', 'temperature', '°F'))).toBe('72.5 °F');
    expect(statusText(s('sensor', 'unavailable'))).toBe('Not reporting');
    // A battery camera asleep is not something detected.
    const sleep = { ...s('binary_sensor', 'on'), entity_id: 'binary_sensor.garage_camera_1_sleep_status' };
    expect(statusText(sleep)).toBe('Asleep');
    expect(statusText({ ...sleep, state: 'off' })).toBe('Awake');
    expect(statusAlert(sleep)).toBe(false);
  });

  it('makes a detection, an open door and a low battery stand out', () => {
    expect(statusAlert(s('binary_sensor', 'on', 'motion'))).toBe(true);
    expect(statusAlert(s('binary_sensor', 'off', 'motion'))).toBe(false);
    expect(statusAlert(s('sensor', '15', 'battery'))).toBe(true);
    expect(statusAlert(s('sensor', '80', 'battery'))).toBe(false);
  });

  it('calls the right service for each action', () => {
    expect(actionService({ domain: 'switch', role: 'opener', state: 'off' })).toEqual(['switch', 'turn_on']);
    expect(actionService({ domain: 'button', role: 'opener', state: 'unknown' })).toEqual(['button', 'press']);
    expect(actionService({ domain: 'lock', role: 'lock', state: 'locked' })).toEqual(['lock', 'unlock']);
    expect(actionService({ domain: 'cover', role: 'cover', state: 'open' })).toEqual(['cover', 'close_cover']);
    expect(actionService({ domain: 'valve', role: 'valve', state: 'closed' })).toEqual(['valve', 'open_valve']);
    expect(actionService({ domain: 'light', role: 'light', state: 'on' })).toEqual(['light', 'turn_off']);
    expect(doorIsOpen({ state: 'on' })).toBe(true);
  });
});
