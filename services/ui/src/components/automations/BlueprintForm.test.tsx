import { describe, expect, it, vi, beforeEach } from 'vitest';
import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BlueprintForm from './BlueprintForm';
import * as automations from '../../lib/automations';
import type { Catalog, Config } from '../../lib/automations';

vi.mock('../../lib/automations', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return { ...real, getBlueprint: vi.fn() };
});

const CATALOG = {
  triggers: [], conditions: [], actions: [], services: [], areas: [], zones: [], persons: [],
  entities: [{ entity_id: 'binary_sensor.hall', name: 'Hall motion', domain: 'binary_sensor' },
    { entity_id: 'light.hall', name: 'Hall light', domain: 'light' }],
} as Catalog;

function Harness({ onChange }: { onChange: (c: Config) => void }) {
  const [config, setConfig] = useState<Config>({ alias: 'Hall', use_blueprint: { path: 'someone/motion.yaml', input: { light: 'light.hall' } } });
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BlueprintForm config={config} catalog={CATALOG} disabled={false} onChange={(c) => { setConfig(c); onChange(c); }} />
    </QueryClientProvider>
  );
}

describe('BlueprintForm', () => {
  beforeEach(() => {
    vi.mocked(automations.getBlueprint).mockResolvedValue({
      path: 'someone/motion.yaml', domain: 'automation', name: 'Light on motion', description: 'Turns a light on',
      inputs: [
        { name: 'sensor', label: 'Motion sensor', description: '', selector: { entity: { domain: 'binary_sensor' } }, section: null, required: true },
        { name: 'light', label: 'Light', description: '', selector: { entity: { domain: 'light' } }, section: null, required: true },
        { name: 'wait', label: 'Wait', description: 'Seconds', selector: { number: { min: 0, max: 600 } }, section: 'Timing', required: false, default: 30 },
        { name: 'quiet', label: 'Quiet at night', description: '', selector: { boolean: {} }, section: 'Timing', required: false, default: false },
      ],
    });
  });

  it('asks for the inputs the blueprint declares and writes only use_blueprint.input', async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    expect(await screen.findByText('Light on motion')).toBeTruthy();
    expect(screen.getByText('Timing')).toBeTruthy();
    expect(screen.getByText(/Default: 30/)).toBeTruthy();
    await userEvent.click(screen.getByRole('textbox', { name: 'Motion sensor' }));
    expect(screen.queryByRole('button', { name: /^Hall light/ })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /^Hall motion/ }));
    await userEvent.type(screen.getByRole('spinbutton', { name: /Wait/ }), '45');
    await userEvent.click(screen.getByRole('switch', { name: 'Quiet at night' }));
    const last = onChange.mock.lastCall?.[0] as Config;
    expect(last).toEqual({ alias: 'Hall', use_blueprint: { path: 'someone/motion.yaml',
      input: { light: 'light.hall', sensor: 'binary_sensor.hall', wait: 45, quiet: true } } });
    await userEvent.clear(screen.getByRole('spinbutton', { name: /Wait/ }));
    expect((onChange.mock.lastCall?.[0] as Config).use_blueprint).toEqual({ path: 'someone/motion.yaml',
      input: { light: 'light.hall', sensor: 'binary_sensor.hall', quiet: true } });
  });

  it('says so when the blueprint cannot be loaded, and still lets the inputs be edited', async () => {
    vi.mocked(automations.getBlueprint).mockRejectedValue(new Error('No blueprint was imported'));
    render(<Harness onChange={vi.fn()} />);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('textbox', { name: /Inputs \(JSON\)/ })).toBeTruthy();
  });
});
