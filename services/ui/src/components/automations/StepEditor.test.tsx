import { describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StepList } from './StepEditor';
import type { Catalog, Step } from '../../lib/automations';

const CATALOG: Catalog = {
  triggers: [
    { type: 'device_onoff', label: 'Device turns on or off', fields: [
      { name: 'entity_id', label: 'Device', kind: 'entity', required: true },
      { name: 'to', label: 'Turns', kind: 'select', options: ['on', 'off'], required: true },
    ] },
    { type: 'time', label: 'At a time', fields: [{ name: 'at', label: 'At', kind: 'time', required: true }] },
  ],
  conditions: [
    { type: 'state', label: 'Entity is in a state', fields: [
      { name: 'entity_id', label: 'Entity', kind: 'entities', required: true },
      { name: 'state', label: 'State', kind: 'text', required: true },
    ] },
    { type: 'template', label: 'Template is true', fields: [{ name: 'value_template', label: 'Template', kind: 'template', required: true }] },
  ],
  actions: [
    { type: 'device', label: 'Make a device do something', fields: [
      { name: 'device_id', label: 'Device', kind: 'device', required: true },
      { name: 'type', label: 'What happens', kind: 'text', required: true },
    ] },
    { type: 'wait_for_trigger', label: 'Wait for a trigger', fields: [
      { name: 'timeout', label: 'Timeout', kind: 'duration' },
      { name: 'continue_on_timeout', label: 'Continue on timeout', kind: 'boolean' },
    ] },
    { type: 'parallel', label: 'Run steps at the same time', fields: [] },
  ],
  devices: [{ id: 'dev1', name: 'Desk lamp', area: 'Study' }],
  services: [{ domain: 'light', services: { turn_on: { fields: { brightness_pct: { name: 'Brightness', selector: { number: {} } } } } } }],
  entities: [
    { entity_id: 'light.porch', name: 'Porch light', domain: 'light' },
    { entity_id: 'switch.fan', name: 'Fan', domain: 'switch' },
  ],
  areas: [], zones: [], persons: [],
};

function Harness({ initial, group, onChange, disabled }: {
  initial: Step[]; group: 'trigger' | 'condition' | 'action'; onChange: (s: Step[]) => void; disabled?: boolean;
}) {
  const [steps, setSteps] = useState(initial);
  return <StepList group={group} steps={steps} catalog={CATALOG} disabled={disabled}
    onChange={(s) => { setSteps(s); onChange(s); }} />;
}

describe('StepList', () => {
  it('adds a trigger from the catalog and fills its fields', async () => {
    const onChange = vi.fn();
    render(<Harness group="trigger" initial={[]} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add trigger' }), 'device_onoff');
    await userEvent.click(screen.getByRole('button', { name: /Add$/ }));
    expect(onChange).toHaveBeenLastCalledWith([{ trigger: 'device_onoff', to: 'on' }]);
    await userEvent.click(screen.getByRole('textbox', { name: 'Device' }));
    await userEvent.click(screen.getByRole('button', { name: /Porch light/ }));
    expect(onChange).toHaveBeenLastCalledWith([{ trigger: 'device_onoff', to: 'on', entity_id: 'light.porch' }]);
  });

  it('edits a service call: entities from its domain, data from its fields', async () => {
    const onChange = vi.fn();
    render(<Harness group="action" initial={[{ action: 'light.turn_on' }]} onChange={onChange} />);
    await userEvent.click(screen.getByRole('textbox', { name: 'Devices' }));
    expect(screen.queryByRole('button', { name: /Fan/ })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /Porch light/ }));
    await userEvent.type(screen.getByRole('spinbutton', { name: /Brightness/ }), '40');
    expect(onChange).toHaveBeenLastCalledWith([{ action: 'light.turn_on', target: { entity_id: 'light.porch' }, data: { brightness_pct: 40 } }]);
  });

  it('keeps an unknown step as JSON', () => {
    render(<Harness group="action" initial={[{ something_new: true }]} onChange={vi.fn()} />);
    expect((screen.getByRole('textbox', { name: /This step as JSON/ }) as HTMLTextAreaElement).value).toContain('something_new');
  });

  it('is read-only when disabled', () => {
    render(<Harness group="trigger" disabled initial={[{ trigger: 'time', at: '07:00:00' }]} onChange={vi.fn()} />);
    expect(screen.queryByRole('combobox', { name: 'Add trigger' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Remove step' })).toBeNull();
    expect(screen.getByLabelText(/^At/)).toBeDisabled();
  });

  it('edits a service call\'s answer variable and carry-on flag', async () => {
    const onChange = vi.fn();
    render(<Harness group="action" initial={[{ action: 'light.turn_on' }]} onChange={onChange} />);
    await userEvent.type(screen.getByRole('textbox', { name: /Keep the answer as/ }), 'r');
    expect(onChange).toHaveBeenLastCalledWith([{ action: 'light.turn_on', response_variable: 'r' }]);
    await userEvent.click(screen.getByRole('switch', { name: 'Carry on if it fails' }));
    expect(onChange).toHaveBeenLastCalledWith([{ action: 'light.turn_on', response_variable: 'r', continue_on_error: true }]);
  });

  it('switches a repeat to while and adds its conditions, keeping the steps', async () => {
    const onChange = vi.fn();
    const seq = [{ action: 'light.turn_on' }];
    render(<Harness group="action" initial={[{ repeat: { count: 2, sequence: seq } }]} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Repeat' }), 'while');
    expect(onChange).toHaveBeenLastCalledWith([{ repeat: { sequence: seq, while: [] } }]);
    await userEvent.selectOptions(screen.getAllByRole('combobox', { name: 'Add condition' })[0], 'template');
    await userEvent.click(screen.getAllByRole('button', { name: /Add$/ })[0]);
    expect(onChange).toHaveBeenLastCalledWith([{ repeat: { sequence: seq, while: [{ condition: 'template' }] } }]);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Repeat' }), 'for_each');
    await userEvent.type(screen.getByRole('textbox', { name: /Items/ }), 'a{enter}b');
    expect(onChange).toHaveBeenLastCalledWith([{ repeat: { sequence: seq, for_each: ['a', 'b'] } }]);
  });

  it('edits a condition used as a step with the condition forms', async () => {
    const onChange = vi.fn();
    render(<Harness group="action" initial={[{ condition: 'template', value_template: '' }]} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Continue only if' }), 'state');
    expect(onChange).toHaveBeenLastCalledWith([{ condition: 'state' }]);
    await userEvent.type(screen.getByRole('textbox', { name: /^State/ }), 'on');
    expect(onChange).toHaveBeenLastCalledWith([{ condition: 'state', state: 'on' }]);
  });

  it('waits for a trigger: triggers nested, timeout from the spec', async () => {
    const onChange = vi.fn();
    render(<Harness group="action" initial={[{ wait_for_trigger: [] }]} onChange={onChange} />);
    expect(screen.getByText('Until one of these happens')).toBeTruthy();
    await userEvent.type(screen.getByRole('textbox', { name: /Timeout/ }), '1');
    expect(onChange).toHaveBeenLastCalledWith([{ wait_for_trigger: [], timeout: '1' }]);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add trigger' }), 'time');
    await userEvent.click(screen.getAllByRole('button', { name: /Add$/ })[0]);
    expect(onChange).toHaveBeenLastCalledWith([{ wait_for_trigger: [{ trigger: 'time' }], timeout: '1' }]);
  });

  it('picks the device of a device step from the catalog', async () => {
    const onChange = vi.fn();
    render(<Harness group="action" initial={[{ device_id: '', domain: 'light', type: '' }]} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /^Device/ }), 'dev1');
    expect(onChange).toHaveBeenLastCalledWith([{ device_id: 'dev1', domain: 'light', type: '' }]);
    expect(screen.getByRole('option', { name: 'Desk lamp (Study)' })).toBeTruthy();
  });

  it('runs steps in parallel as a nested list', async () => {
    const onChange = vi.fn();
    render(<Harness group="action" initial={[{ parallel: [{ action: 'light.turn_on' }] }]} onChange={onChange} />);
    expect(screen.getByText('All at the same time')).toBeTruthy();
    expect(screen.getAllByTestId('step-action')).toHaveLength(2);
  });
});
