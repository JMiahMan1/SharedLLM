import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import TasmotaPinFinder from './TasmotaPinFinder';
import * as api from '../../lib/tasmotaTemplates';
import type { PinFinderQuestion, PinFinderState } from '../../lib/tasmotaTemplates';

vi.mock('../../lib/tasmotaTemplates', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  getPinFinder: vi.fn(),
  startPinFinder: vi.fn(),
  answerPinFinder: vi.fn(),
  cancelPinFinder: vi.fn(),
  saveMeasuredTemplate: vi.fn(),
}));

const FOUND = '{"NAME":"Example A1","GPIO":[0,0,0,0,0,416,0,0,0,449,0,0,0,0],"FLAG":0,"BASE":18,"CMND":"SO92 1"}';

const idle = (over: Partial<PinFinderState> = {}): PinFinderState => ({
  active: false, status: 'idle', question: null, result: null, message: null, interrupted: false,
  kinds: ['bulb', 'strip', 'dimmer'], ...over,
});

const question = (over: Partial<PinFinderQuestion> = {}): PinFinderQuestion => ({
  step: 2, id: 'single', text: 'Only GPIO4 is on now. What does Den lamp look like?', hint: 'Dark is a fine answer.',
  pin: 4, again: true, progress: { done: 0, total: 5 },
  answers: [{ value: 'dark', label: 'Dark' }, { value: 'cool', label: 'Cool white' }, { value: 'warm', label: 'Warm white' }],
  ...over,
});

const asking = (q: PinFinderQuestion = question()): PinFinderState =>
  idle({ active: true, status: 'asking', question: q, manufacturer: 'Example', model: 'A1', kind: 'bulb' });

function renderFinder(first = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><TasmotaPinFinder entryId="t1" deviceName="Den lamp" first={first} /></QueryClientProvider>);
}

async function confirmIn(title: RegExp, answer: string) {
  const dialog = await screen.findByRole('dialog', { name: title });
  fireEvent.click(within(dialog).getByRole('button', { name: answer }));
}

describe('TasmotaPinFinder', () => {
  beforeEach(() => vi.clearAllMocks());

  it('asks before starting, says what happens, and sends nothing on Cancel', async () => {
    vi.mocked(api.getPinFinder).mockResolvedValue(idle());
    renderFinder();
    const startButton = await screen.findByRole('button', { name: 'Start the pin finder' });
    await waitFor(() => expect(startButton).toBeEnabled());
    fireEvent.click(startButton);
    const dialog = await screen.findByRole('dialog', { name: 'Find the pins of Den lamp?' });
    expect(dialog).toHaveTextContent("Den lamp's template is saved first");
    expect(dialog).toHaveTextContent('Cancel at any point puts the saved template back.');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(api.startPinFinder).not.toHaveBeenCalled();
  });

  it('starts with the maker and model, then answers each question by its step', async () => {
    vi.mocked(api.getPinFinder).mockResolvedValue(idle());
    vi.mocked(api.startPinFinder).mockResolvedValue(asking(question({ step: 1, id: 'baseline', text: 'Is Den lamp dark?',
      pin: null, answers: [{ value: 'dark', label: 'Yes, it is dark' }, { value: 'lit', label: 'No, it is lit' }] })));
    vi.mocked(api.answerPinFinder).mockResolvedValueOnce(asking()).mockResolvedValueOnce(asking());
    renderFinder();
    await screen.findByTestId('pin-finder-intro');
    fireEvent.change(screen.getByLabelText('Manufacturer'), { target: { value: 'Example ' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'A1' } });
    fireEvent.change(screen.getByLabelText('What it is'), { target: { value: 'strip' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start the pin finder' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Start the pin finder' }));
    await confirmIn(/Find the pins/, 'Save and start');
    await waitFor(() => expect(api.startPinFinder).toHaveBeenCalledWith('t1', { manufacturer: 'Example', model: 'A1', kind: 'strip' }));
    expect(await screen.findByTestId('pin-finder-question')).toHaveTextContent('Is Den lamp dark?');
    expect(screen.queryByTestId('pin-finder-intro')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Yes, it is dark' }));
    await waitFor(() => expect(api.answerPinFinder).toHaveBeenCalledWith('t1', 1, 'dark'));
    expect(await screen.findByText('Only GPIO4 is on now. What does Den lamp look like?')).toBeInTheDocument();
    expect(screen.getByText('Pins tried: 0 of 5')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show again' }));
    await waitFor(() => expect(api.answerPinFinder).toHaveBeenLastCalledWith('t1', 2, 'again'));
  });

  it('cancels after a confirm and says the template was put back', async () => {
    vi.mocked(api.getPinFinder).mockResolvedValue(asking());
    vi.mocked(api.cancelPinFinder).mockResolvedValue(idle({ status: 'cancelled', restored: true,
      message: 'Cancelled. Put back as it was (Generic).' }));
    renderFinder(false);
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel and put it back' }));
    await confirmIn(/Stop and put Den lamp back/, 'Put it back');
    expect(await screen.findByTestId('pin-finder-ended')).toHaveTextContent('Put back as it was (Generic).');
    expect(screen.getByRole('button', { name: 'Start again' })).toBeInTheDocument();
  });

  it('shows the template it found and saves it as measured', async () => {
    vi.mocked(api.getPinFinder).mockResolvedValue(idle({ status: 'done', manufacturer: 'Example', model: 'A1', kind: 'bulb',
      message: 'Found and checked. The device now runs this template.',
      result: { template: FOUND, commands: ['SO92 1'], type: 'ct', pins: { bright: 5, ct: 13 }, invert: true, kind: 'bulb',
        how: 'Found by the pin finder: GPIO5 alone lit it.' } }));
    vi.mocked(api.saveMeasuredTemplate).mockResolvedValue({ status: 'saved', message: 'Saved. Measured on Den lamp.',
      template: { id: 'measured-example-a1-00000000', manufacturer: 'Example', model: 'A1', kind: 'bulb', chip: 'ESP8266',
        template: FOUND, commands: ['SO92 1'], source_url: '', notes: '', name: 'Example A1', channels: 2, source: 'measured' } });
    renderFinder(false);
    const done = await screen.findByTestId('pin-finder-done');
    expect(within(done).getByTestId('pin-finder-template')).toHaveTextContent(FOUND);
    expect(done).toHaveTextContent('GPIO5 alone lit it');
    expect(within(done).getByLabelText('Model')).toHaveValue('A1');
    fireEvent.click(within(done).getByRole('button', { name: 'Save as measured' }));
    await waitFor(() => expect(api.saveMeasuredTemplate).toHaveBeenCalledWith('t1', { manufacturer: 'Example', model: 'A1', kind: 'bulb' }));
    expect(await screen.findByTestId('pin-finder-saved')).toHaveTextContent('Measured on Den lamp');
  });

  it('offers to put back the template of a run that did not finish', async () => {
    vi.mocked(api.getPinFinder).mockResolvedValue(idle({ interrupted: true,
      message: 'A pin finder run did not finish. Its saved template can be put back.' }));
    vi.mocked(api.cancelPinFinder).mockResolvedValue(idle({ status: 'cancelled', restored: true, message: 'Put back.' }));
    renderFinder(false);
    const box = await screen.findByTestId('pin-finder-interrupted');
    expect(screen.queryByRole('button', { name: 'Start the pin finder' })).toBeNull();
    fireEvent.click(within(box).getByRole('button', { name: 'Put the saved template back' }));
    await confirmIn(/Stop and put Den lamp back/, 'Put it back');
    await waitFor(() => expect(api.cancelPinFinder).toHaveBeenCalledWith('t1'));
  });

  it('stays folded on a device a template already fits until opened', async () => {
    vi.mocked(api.getPinFinder).mockResolvedValue(idle());
    renderFinder(false);
    const toggle = await screen.findByRole('button', { name: /Find my pins/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('pin-finder-intro')).toBeNull();
    fireEvent.click(toggle);
    expect(await screen.findByTestId('pin-finder-intro')).toHaveTextContent('Plugs and switches are not covered yet');
  });
});
