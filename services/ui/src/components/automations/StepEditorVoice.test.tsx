import { describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/setup';
import { StepList } from './StepEditor';
import type { Catalog, Step } from '../../lib/automations';

/**
 * The visual editor's voice steps: a sentence trigger, the reply to it, a voice satellite's timer, and the voice
 * a spoken message uses (Jarvis's Kokoro voices and alpaca's cloned voices). Every name is made up.
 */

const CATALOG: Catalog = {
  triggers: [{ type: 'conversation', label: 'Someone says a sentence',
    fields: [{ name: 'command', label: 'Sentences', kind: 'lines', required: true }] }],
  conditions: [],
  actions: [
    { type: 'set_conversation_response', label: 'Reply to the person speaking',
      fields: [{ name: 'set_conversation_response', label: 'Reply', kind: 'template', required: true }] },
    { type: 'satellite_timer', label: 'Show a timer on a voice satellite', service: 'assist_satellite.timer_event', fields: [] },
  ],
  services: [
    { domain: 'assist_satellite', services: { timer_event: { fields: {
      event: { name: 'Event', required: true, selector: { select: { options: ['started', 'updated', 'cancelled', 'finished'] } } },
      timer_id: { name: 'Timer id', required: true, selector: { text: {} } },
      total_seconds: { name: 'Length (seconds)', selector: { number: {} } },
    } } } },
    { domain: 'tts', services: { speak: { fields: { media_player_entity_id: {}, message: {}, options: {} } } } },
  ],
  entities: [
    { entity_id: 'assist_satellite.den_voice', name: 'Den voice', domain: 'assist_satellite' },
    { entity_id: 'tts.jarvis', name: 'Jarvis', domain: 'tts' },
  ],
  areas: [], zones: [], persons: [],
};

function Harness({ initial, group, onChange }: {
  initial: Step[]; group: 'trigger' | 'action'; onChange: (s: Step[]) => void;
}) {
  const [steps, setSteps] = useState(initial);
  return <StepList group={group} steps={steps} catalog={CATALOG} onChange={(s) => { setSteps(s); onChange(s); }} />;
}

const VOICES = {
  voices: [
    { id: 'af_heart', name: 'af_heart', kind: 'kokoro' },
    { id: 'am_adam', name: 'am_adam', kind: 'kokoro' },
    { id: 'pat-0a1b2c', name: 'Pat', kind: 'clone' },
  ],
  default: 'af_heart',
  errors: {},
};

describe('voice steps in the visual editor', () => {
  it('adds a sentence trigger and writes one sentence per line', async () => {
    const onChange = vi.fn();
    render(<Harness group="trigger" initial={[]} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add trigger' }), 'conversation');
    await userEvent.click(screen.getByRole('button', { name: /Add$/ }));
    expect(onChange).toHaveBeenLastCalledWith([{ trigger: 'conversation', command: [] }]);
    await userEvent.type(screen.getByRole('textbox', { name: /Sentences/ }), 'good night{enter}[[it is] bed time');
    expect(onChange).toHaveBeenLastCalledWith([{ trigger: 'conversation', command: ['good night', '[it is] bed time'] }]);
    expect(screen.getByText('When someone says "good night" or 1 more')).toBeTruthy();
  });

  it('adds the reply to the person speaking', async () => {
    const onChange = vi.fn();
    render(<Harness group="action" initial={[]} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add action' }), 'set_conversation_response');
    await userEvent.click(screen.getByRole('button', { name: /Add$/ }));
    await userEvent.type(screen.getByRole('textbox', { name: /Reply/ }), 'Sleep well');
    expect(onChange).toHaveBeenLastCalledWith([{ set_conversation_response: 'Sleep well' }]);
    expect(screen.getByText('Reply: Sleep well')).toBeTruthy();
  });

  it('tells a voice satellite about a timer with the service\'s own fields', async () => {
    const onChange = vi.fn();
    render(<Harness group="action" initial={[]} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add action' }), 'satellite_timer');
    await userEvent.click(screen.getByRole('button', { name: /Add$/ }));
    expect(onChange).toHaveBeenLastCalledWith([{ action: 'assist_satellite.timer_event', data: { event: 'started' } }]);
    await userEvent.click(screen.getByRole('textbox', { name: 'Voice satellite' }));
    await userEvent.click(screen.getByRole('button', { name: /Den voice/ }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /Event/ }), 'finished');
    await userEvent.type(screen.getByRole('textbox', { name: /Timer id/ }), 't1');
    expect(onChange).toHaveBeenLastCalledWith([{ action: 'assist_satellite.timer_event',
      target: { entity_id: 'assist_satellite.den_voice' }, data: { event: 'finished', timer_id: 't1' } }]);
    expect(screen.getByText('Timer finished on Den voice')).toBeTruthy();
  });

  it('offers Jarvis\'s voices and the cloned ones for a spoken message', async () => {
    server.use(http.get('/api/homehub/automations/voices', () => HttpResponse.json(VOICES)));
    const onChange = vi.fn();
    render(<Harness group="action" initial={[{ action: 'tts.speak', data: { message: 'Dinner' } }]} onChange={onChange} />);
    const voice = await screen.findByRole('combobox', { name: /Voice/ });
    await screen.findByRole('option', { name: 'Pat' });
    expect(screen.getByRole('group', { name: 'Cloned voices' })).toBeTruthy();
    expect(screen.queryByRole('textbox', { name: /^options/ })).toBeNull();
    await userEvent.selectOptions(voice, 'pat-0a1b2c');
    expect(onChange).toHaveBeenLastCalledWith([{ action: 'tts.speak', data: { message: 'Dinner', options: { voice: 'pat-0a1b2c' } } }]);
    await userEvent.selectOptions(voice, '');
    expect(onChange).toHaveBeenLastCalledWith([{ action: 'tts.speak', data: { message: 'Dinner' } }]);
  });

  it('says why cloned voices are missing and keeps a voice it does not know', async () => {
    server.use(http.get('/api/homehub/automations/voices', () => HttpResponse.json({
      voices: [{ id: 'af_heart', name: 'af_heart', kind: 'kokoro' }], default: 'af_heart',
      errors: { clone: 'a cloned voice is spoken by alpaca\'s audio server, and ALPACA_WEB_URL is not set' } })));
    render(<Harness group="action" initial={[{ action: 'tts.speak', data: { options: { voice: 'Lee' } } }]} onChange={vi.fn()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Cloned voices: a cloned voice is spoken by alpaca');
    expect(screen.getByRole('option', { name: 'Lee (not found)' })).toBeTruthy();
  });
});
