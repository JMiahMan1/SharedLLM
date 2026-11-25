import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import WakeWordSection from '../components/devices/WakeWordSection';
import type { HardwareEntity } from '../lib/hardware';
import type { HubEntity } from '../lib/dashboards';
import { detectionTime, probabilityText, wakeWordEntityIds, wakeWordInfo } from '../lib/wakeWord';
import { server } from './setup';

// Made-up Echo: no real household names, addresses or devices.
const WARNING = 'More sensitive means more accidental wake-ups, especially near a TV or radio.';

function hubState(entity_id: string, state: string, attributes: Record<string, unknown>): HubEntity {
  return {
    entity_id, domain: entity_id.split('.')[0], name: entity_id, state, attributes,
    device_class: null, unit: null, icon: null, area_id: null, device_id: 'dev-echo', last_changed: '2026-11-20T18:00:00Z',
  };
}

function entity(entity_id: string, state: HubEntity | null, category: string): HardwareEntity {
  return {
    entity_id, domain: entity_id.split('.')[0], platform: 'esphome', name: entity_id, name_by_user: null,
    original_name: null, entity_category: category, disabled_by: null, hidden_by: null, area_id: null, icon: null,
    state, capabilities: null,
  };
}

const SELECT_ID = 'select.test_echo_wake_word_sensitivity';
const SENSOR_ID = 'sensor.test_echo_wake_word_detections';

function echo(level = 'More sensitive', extra: Record<string, unknown> = {}): HardwareEntity[] {
  return [
    entity(SELECT_ID, hubState(SELECT_ID, level, {
      options: ['Standard', 'More sensitive', 'Most sensitive'], warning: WARNING, managed: true,
      models: { hey_jarvis: { cutoff: 0.94, window: 5 } }, ...extra,
    }), 'config'),
    entity(SENSOR_ID, hubState(SENSOR_ID, '2', {
      detections: [
        { at: '2026-11-20 18:02:11.417Z', wake_word: 'Hey Jarvis', probability: 0.953, cutoff: 0.94, second: false },
        { at: 'boot+812.004', wake_word: 'Alexa', probability: 0.914, second: true },
        { at: '', wake_word: 'broken', probability: 'x' },
      ],
    }), 'diagnostic'),
    entity('select.test_echo_wake_word_engine', hubState('select.test_echo_wake_word_engine', 'microWakeWord', {}), 'config'),
  ];
}

describe('wakeWordInfo', () => {
  it('reads the sensitivity, its warning and the wake-ups, skipping a malformed one', () => {
    const info = wakeWordInfo(echo());
    expect(info?.sensitivity?.state).toBe('More sensitive');
    expect(info?.warning).toBe(WARNING);
    expect(info?.detections).toEqual([
      { at: '2026-11-20 18:02:11.417Z', wakeWord: 'Hey Jarvis', probability: 0.953, cutoff: 0.94, second: false },
      { at: 'boot+812.004', wakeWord: 'Alexa', probability: 0.914, cutoff: null, second: true },
    ]);
    expect([...wakeWordEntityIds(echo())].sort()).toEqual([SELECT_ID, SENSOR_ID].sort());
  });

  it('is null for a device that is not a hassmic Echo', () => {
    expect(wakeWordInfo([entity('light.lamp', null, 'config')])).toBeNull();
  });

  it('formats the times and probabilities', () => {
    expect(detectionTime('boot+812.004')).toBe('14 min after it started');
    expect(detectionTime('boot+30')).toBe('30 s after it started');
    expect(detectionTime('not a time')).toBe('not a time');
    expect(detectionTime('2026-11-20 18:02:11.417Z', 'en-US')).toMatch(/Nov 20/);
    expect(probabilityText({ at: 'x', wakeWord: 'Alexa', probability: 0.914, cutoff: null, second: false })).toBe('0.91');
    expect(probabilityText({ at: 'x', wakeWord: 'Alexa', probability: 0.953, cutoff: 0.94, second: false }))
      .toBe('0.95 (cutoff 0.94)');
  });
});

describe('WakeWordSection', () => {
  it('shows the level, the warning and the recent wake-ups', () => {
    render(<WakeWordSection entities={echo()} />);
    const section = screen.getByRole('region', { name: 'Wake word' });
    expect(within(section).getByRole('combobox', { name: 'Sensitivity' })).toHaveValue('More sensitive');
    expect(within(section).getByRole('note')).toHaveTextContent(WARNING);
    const rows = within(section).getAllByTestId('wake-detection');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Hey Jarvis');
    expect(rows[0]).toHaveTextContent('0.95 (cutoff 0.94)');
    expect(rows[1]).toHaveTextContent('Alexa (second)');
    expect(rows[1]).toHaveTextContent('14 min after it started');
  });

  it('changes the level through the hub select and says why it failed', async () => {
    const calls: Record<string, unknown>[] = [];
    server.use(http.post('/execute/ha_service', async ({ request }) => {
      calls.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ status: 'SUCCESS' });
    }));
    const onChanged = vi.fn();
    render(<WakeWordSection entities={echo('Standard', { reason: 'the Echo did not take the values for alexa' })}
      onChanged={onChanged} />);
    expect(screen.getByRole('alert')).toHaveTextContent('the Echo did not take the values for alexa');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Sensitivity' }), 'Most sensitive');
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ domain: 'select', service: 'select_option', entity_id: SELECT_ID });
    expect(JSON.stringify(calls[0])).toContain('Most sensitive');
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('says so when nothing has been heard yet', () => {
    const entities = echo();
    entities[1] = entity(SENSOR_ID, hubState(SENSOR_ID, '0', { detections: [] }), 'diagnostic');
    render(<WakeWordSection entities={entities} />);
    expect(screen.getByText(/None heard since HomeHub started/)).toBeInTheDocument();
  });

  it('renders nothing for another device', () => {
    const { container } = render(<WakeWordSection entities={[entity('light.lamp', null, 'config')]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
