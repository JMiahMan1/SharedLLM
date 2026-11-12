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
  conditions: [],
  actions: [],
  services: [{ domain: 'light', services: { turn_on: { fields: { brightness_pct: { name: 'Brightness', selector: { number: {} } } } } } }],
  entities: [
    { entity_id: 'light.porch', name: 'Porch light', domain: 'light' },
    { entity_id: 'switch.fan', name: 'Fan', domain: 'switch' },
  ],
  areas: [], zones: [], persons: [],
};

function Harness({ initial, group, onChange, disabled }: {
  initial: Step[]; group: 'trigger' | 'action'; onChange: (s: Step[]) => void; disabled?: boolean;
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
});
