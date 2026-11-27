import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import TasmotaTemplateSection from './TasmotaTemplateSection';
import * as api from '../../lib/tasmotaTemplates';
import {
  applyRequestFor, filterCatalog, measuredLabel, pasteProblem, templateName,
  type ApplyResult, type CatalogTemplate, type DeviceTemplate, type Suggestion,
} from '../../lib/tasmotaTemplates';

vi.mock('../../lib/tasmotaTemplates', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  getDeviceTemplate: vi.fn(),
  getTemplateCatalog: vi.fn(),
  applyTemplate: vi.fn(),
  restorePreviousTemplate: vi.fn(),
  getPinFinder: vi.fn(),
  saveMeasuredTemplate: vi.fn(),
  forgetMeasuredTemplate: vi.fn(),
}));

const KAUF = '{"NAME":"Kauf Bulb","GPIO":[0,0,0,0,416,419,0,0,417,420,418,0,0,0],"FLAG":0,"BASE":18}';
const GENERIC = '{"NAME":"Generic","GPIO":[1,1,1,1,1,1,0,0,1,1,1,1,1,0],"FLAG":0,"BASE":18}';

const entry = (over: Partial<CatalogTemplate> = {}): CatalogTemplate => ({
  id: 'kauf-blf15', manufacturer: 'KAUF', model: 'BLF15 A15 5W RGBCCT bulb', kind: 'bulb', chip: 'ESP8266',
  template: KAUF, commands: [], source_url: 'https://templates.blakadder.com/kauf_BLF15.html', notes: 'E26.',
  name: 'Kauf Bulb', channels: 5, ...over,
});
const PLUG = entry({ id: 'made-up-plug', manufacturer: 'Example', model: 'P1 plug', kind: 'plug', name: 'P1', channels: 0 });

const suggestion = (over: Partial<Suggestion> = {}): Suggestion => ({
  ...entry(), score: 9, reasons: ['Its name mentions KAUF', 'Its name says it is a bulb'], ...over,
});

const state = (over: Partial<DeviceTemplate> = {}): DeviceTemplate => ({
  entry_id: 't1', title: 'Porch bulb',
  current: {
    device_name: 'Kauf Bulb', friendly_name: ['Kauf Bulb'], hostname: 'kauf-bulb-0a01', chip: 'ESP8266EX',
    firmware: '15.6.0(release-tasmota)', module: 18, module_name: 'Generic', template_name: 'Generic',
    template_json: GENERIC, active: false, light: false, light_channels: 0, relays: 0, energy: false, catalog_matches: [],
  },
  up_to_date: false, message: null, previous_template: null,
  suggestions: [suggestion(), suggestion({ id: 'kauf-blf19', model: 'BLF19 A19 7W RGBCCT bulb', score: 8 })],
  ...over,
});

const applied = (over: Partial<ApplyResult> = {}): ApplyResult => ({
  status: 'applied', verified: true, restarted: true, sent: `Backlog Template ${KAUF}; Module 0`, template: KAUF,
  previous_template: GENERIC, message: 'Loaded and verified: the device restarted with the new template.',
  entities_added: [], entities_stale: [], current: null, ...over,
});

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><TasmotaTemplateSection entryId="t1" deviceName="Porch bulb" /></QueryClientProvider>);
}

async function confirmIn(title: RegExp, answer: 'Load and restart' | 'Restore and restart' | 'Cancel') {
  const dialog = await screen.findByRole('dialog', { name: title });
  fireEvent.click(within(dialog).getByRole('button', { name: answer }));
}

describe('TasmotaTemplateSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getTemplateCatalog).mockResolvedValue([entry(), PLUG]);
    vi.mocked(api.getPinFinder).mockResolvedValue({ active: false, status: 'idle', question: null, result: null,
      message: null, interrupted: false, kinds: ['bulb', 'strip', 'dimmer'] });
  });

  it('shows the current template and the suggestions with their reasons', async () => {
    vi.mocked(api.getDeviceTemplate).mockResolvedValue(state());
    renderSection();
    const current = await screen.findByTestId('template-current');
    expect(current).toHaveTextContent('Now: Generic (not in use; it runs the Generic module)');
    expect(current).toHaveTextContent('ESP8266EX · drives nothing yet');
    const rows = screen.getAllByTestId('template-suggestion');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('KAUF BLF15 A15 5W RGBCCT bulb');
    expect(rows[0]).toHaveTextContent('Its name mentions KAUF');
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /Restore previous/ })).toBeNull();
  });

  it('asks before loading, naming the device and the template, and sends nothing on Cancel', async () => {
    vi.mocked(api.getDeviceTemplate).mockResolvedValue(state());
    renderSection();
    const rows = await screen.findAllByTestId('template-suggestion');
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Choose' }));
    expect(screen.getByTestId('template-chosen')).toHaveTextContent('Chosen: KAUF BLF15 A15 5W RGBCCT bulb');
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    const dialog = await screen.findByRole('dialog', { name: 'Load this template onto Porch bulb?' });
    expect(dialog).toHaveTextContent('Porch bulb gets the KAUF BLF15 A15 5W RGBCCT bulb template.');
    expect(dialog).toHaveTextContent('The device restarts');
    expect(dialog).toHaveTextContent('Its current template ("Generic") is kept so it can be restored.');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(api.applyTemplate).not.toHaveBeenCalled();
  });

  it('loads the chosen template on confirm, shows it was verified and offers Restore previous', async () => {
    vi.mocked(api.getDeviceTemplate).mockResolvedValue(state());
    vi.mocked(api.applyTemplate).mockResolvedValue(applied());
    vi.mocked(api.restorePreviousTemplate).mockResolvedValue(applied({ template: GENERIC, previous_template: KAUF,
      message: 'Loaded and verified: the device restarted with the new template.' }));
    renderSection();
    const rows = await screen.findAllByTestId('template-suggestion');
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Choose' }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await confirmIn(/Load this template/, 'Load and restart');
    await waitFor(() => expect(api.applyTemplate).toHaveBeenCalledWith('t1', { catalog_id: 'kauf-blf15' }));
    expect(await screen.findByTestId('template-result')).toHaveTextContent('Loaded and verified');
    fireEvent.click(screen.getByRole('button', { name: /Restore previous/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Restore the previous template on Porch bulb?' });
    expect(dialog).toHaveTextContent('("Generic")');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Restore and restart' }));
    await waitFor(() => expect(api.restorePreviousTemplate).toHaveBeenCalledWith('t1'));
  });

  it('says when the template could not be verified', async () => {
    vi.mocked(api.getDeviceTemplate).mockResolvedValue(state());
    vi.mocked(api.applyTemplate).mockResolvedValue(applied({ verified: false,
      message: 'The template was sent, but the device reports a different one; it was not verified.' }));
    renderSection();
    fireEvent.click(within((await screen.findAllByTestId('template-suggestion'))[0]).getByRole('button', { name: 'Choose' }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await confirmIn(/Load this template/, 'Load and restart');
    expect(await screen.findByTestId('template-result')).toHaveTextContent('it was not verified');
  });

  it('checks a pasted template before it can be applied, then sends it as pasted', async () => {
    vi.mocked(api.getDeviceTemplate).mockResolvedValue(state({ suggestions: [] }));
    vi.mocked(api.applyTemplate).mockResolvedValue(applied());
    renderSection();
    await screen.findByTestId('template-current');
    const box = screen.getByTestId('template-paste');
    fireEvent.change(box, { target: { value: '{"NAME":"Porch"' } });
    expect(screen.getByRole('alert')).toHaveTextContent('not valid JSON');
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();
    const pasted = ' {"NAME":"Porch","GPIO":[0,0,0,0,416,419,0,0,417,420,418,0,0,0],"FLAG":0,"BASE":18} ';
    fireEvent.change(box, { target: { value: pasted } });
    expect(screen.getByTestId('template-chosen')).toHaveTextContent('Chosen: Porch');
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await confirmIn(/Load this template/, 'Load and restart');
    await waitFor(() => expect(api.applyTemplate).toHaveBeenCalledWith('t1', { template: pasted.trim() }));
  });

  it('searches the catalog by words and kind', async () => {
    vi.mocked(api.getDeviceTemplate).mockResolvedValue(state({ suggestions: [] }));
    renderSection();
    await screen.findByTestId('template-current');
    const cat = screen.getByTestId('template-catalog');
    fireEvent.click(within(cat).getByText(/Choose from the catalog/));
    await waitFor(() => expect(within(cat).getAllByTestId('template-option')).toHaveLength(2));
    fireEvent.change(within(cat).getByLabelText('Search templates'), { target: { value: 'kauf' } });
    expect(within(cat).getAllByTestId('template-option')).toHaveLength(1);
    fireEvent.change(within(cat).getByLabelText('Search templates'), { target: { value: '' } });
    fireEvent.change(within(cat).getByLabelText('Kind of device'), { target: { value: 'plug' } });
    const only = within(cat).getAllByTestId('template-option');
    expect(only).toHaveLength(1);
    fireEvent.click(within(only[0]).getByRole('button', { name: 'Choose' }));
    expect(screen.getByTestId('template-chosen')).toHaveTextContent('Chosen: Example P1 plug');
  });

  it('says when the device already has a catalog template', async () => {
    vi.mocked(api.getDeviceTemplate).mockResolvedValue(state({
      up_to_date: true, message: 'It already has the KAUF BLF10 10W RGBCCT bulb template: nothing to do.',
      previous_template: GENERIC,
      current: { ...state().current, module: 0, template_name: 'Kauf Bulb', active: true, light: true, light_channels: 5 },
    }));
    renderSection();
    expect(await screen.findByTestId('template-up-to-date')).toHaveTextContent('nothing to do');
    expect(screen.getByTestId('template-current')).toHaveTextContent('a light with 5 channels');
    expect(screen.getByRole('button', { name: /Restore previous/ })).toBeEnabled();
  });

  it('says when nothing identifies the device and offers the pin finder before any guess', async () => {
    vi.mocked(api.getDeviceTemplate).mockResolvedValue(state({
      identified: false, suggestions: [],
      identify_note: 'It reports nothing that identifies it: it runs the Generic module with no pins set.',
      guesses: [suggestion({ reasons: ['Its name says it is a bulb'] })],
    }));
    renderSection();
    expect(await screen.findByTestId('template-unidentified')).toHaveTextContent('nothing that identifies it');
    const finder = screen.getByTestId('pin-finder');
    expect(within(finder).getByRole('button', { name: 'Start the pin finder' })).toBeInTheDocument();
    expect(screen.queryAllByTestId('template-suggestion')).toHaveLength(0);
    const guesses = screen.getByTestId('template-guesses');
    expect(guesses).not.toHaveAttribute('open');
    expect(guesses).toHaveTextContent('Guesses from its name only (1)');
    // The pin finder comes before the guesses on the page.
    expect(finder.compareDocumentPosition(guesses) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('labels a measured template with the device it was measured on and loads it by its id', async () => {
    const measured = suggestion({ id: 'measured-lotton-a19-cct-1a2b3c4d', manufacturer: 'Lotton', model: 'A19 CCT',
      name: 'Lotton A19 CCT', channels: 2, source: 'measured', measured_on: 'Den lamp', measured_at: '2026-11-24',
      reasons: ['Its name matches the model measured on Den lamp'] });
    vi.mocked(api.getDeviceTemplate).mockResolvedValue(state({ identified: true, suggestions: [measured, suggestion()] }));
    vi.mocked(api.applyTemplate).mockResolvedValue(applied());
    renderSection();
    const rows = await screen.findAllByTestId('template-suggestion');
    expect(within(rows[0]).getByTestId('measured-label')).toHaveTextContent('Measured on Den lamp, 2026-11-24');
    expect(within(rows[1]).queryByTestId('measured-label')).toBeNull();
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Choose' }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await confirmIn(/Load this template/, 'Load and restart');
    await waitFor(() => expect(api.applyTemplate).toHaveBeenCalledWith('t1', { measured_id: 'measured-lotton-a19-cct-1a2b3c4d' }));
  });

  it('saves the template of a working device as measured on it', async () => {
    vi.mocked(api.getDeviceTemplate).mockResolvedValue(state({
      current: { ...state().current, module: 0, template_name: 'Porch CCT', template_json: KAUF, active: true,
        light: true, light_channels: 2, measured_matches: [] },
    }));
    vi.mocked(api.saveMeasuredTemplate).mockResolvedValue({ status: 'saved', template: entry({ source: 'measured' }),
      message: 'Saved. Devices like it will be offered this template, marked "Measured on Porch bulb".' });
    renderSection();
    const box = await screen.findByTestId('template-save-device');
    fireEvent.click(within(box).getByText("Save this device's template"));
    const save = within(box).getByRole('button', { name: 'Save as measured' });
    expect(save).toBeDisabled();
    fireEvent.change(within(box).getByLabelText('Manufacturer'), { target: { value: 'Example' } });
    fireEvent.change(within(box).getByLabelText('Model'), { target: { value: ' A1 ' } });
    fireEvent.click(save);
    await waitFor(() => expect(api.saveMeasuredTemplate).toHaveBeenCalledWith('t1',
      { manufacturer: 'Example', model: 'A1', kind: 'bulb' }));
    expect(await within(box).findByRole('status')).toHaveTextContent('Measured on Porch bulb');
  });

  it('does not offer to save a template the household already measured', async () => {
    vi.mocked(api.getDeviceTemplate).mockResolvedValue(state({
      current: { ...state().current, module: 0, template_name: 'Porch CCT', template_json: KAUF, active: true,
        light: true, light_channels: 2,
        measured_matches: [{ id: 'measured-x', manufacturer: 'Example', model: 'A1', measured_on: 'Porch bulb' }] },
    }));
    renderSection();
    await screen.findByTestId('template-current');
    expect(screen.queryByTestId('template-save-device')).toBeNull();
  });

  it('shows why the device could not be read', async () => {
    vi.mocked(api.getDeviceTemplate).mockRejectedValue(new AxiosError('x', '503', undefined, undefined,
      { status: 503, data: { detail: 'Tasmota 192.0.2.51 unreachable' } } as never));
    renderSection();
    expect(await screen.findByRole('alert')).toHaveTextContent('unreachable');
  });
});

describe('tasmotaTemplates helpers', () => {
  it('reads a template name and finds what is wrong with a paste', () => {
    expect(templateName(KAUF)).toBe('Kauf Bulb');
    expect(templateName('nope')).toBeNull();
    expect(pasteProblem('')).toMatch('Paste a template');
    expect(pasteProblem('[1]')).toMatch('JSON object');
    expect(pasteProblem('{"GPIO":[1],"BASE":1}')).toMatch('no NAME');
    expect(pasteProblem('{"NAME":"x","BASE":1}')).toMatch('no GPIO');
    expect(pasteProblem('{"NAME":"x","GPIO":[1.5],"BASE":1}')).toMatch('whole number');
    expect(pasteProblem('{"NAME":"x","GPIO":[1]}')).toMatch('no BASE');
    expect(pasteProblem(KAUF)).toBeNull();
  });

  it('labels measured templates and picks the request that loads each', () => {
    expect(measuredLabel({ source: 'measured', measured_on: 'Den lamp' })).toBe('Measured on Den lamp');
    expect(measuredLabel({ source: 'measured', measured_on: null })).toBe('Measured here');
    expect(measuredLabel({ source: 'catalog' })).toBeNull();
    expect(applyRequestFor(entry({ source: 'measured', id: 'measured-a' }))).toEqual({ measured_id: 'measured-a' });
    expect(applyRequestFor(entry())).toEqual({ catalog_id: 'kauf-blf15' });
  });

  it('filters the catalog by every word and the kind', () => {
    expect(filterCatalog([entry(), PLUG], 'kauf blf15', '')).toHaveLength(1);
    expect(filterCatalog([entry(), PLUG], '', 'plug')).toEqual([PLUG]);
    expect(filterCatalog([entry(), PLUG], 'nothing', '')).toEqual([]);
  });
});
