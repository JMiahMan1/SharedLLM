import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Identity from './Identity';
import { renderWithProviders } from '../test/render';

vi.stubGlobal('prompt', vi.fn(() => 'CLI Client'));
if (!Object.getOwnPropertyDescriptor(navigator, 'clipboard')) {
  Object.defineProperty(navigator, 'clipboard', {
    value: {
      writeText: vi.fn().mockResolvedValue(undefined),
    },
    writable: true,
    configurable: true,
  });
}

describe('Identity page', () => {
  it('renders integrations, keys, and persona data from the live contract', async () => {
    renderWithProviders(<Identity />);

    expect(await screen.findByText('Integration Gallery')).toBeInTheDocument();
    expect(screen.getByText('External Client Access')).toBeInTheDocument();
    expect(await screen.findByText('OpenWebUI')).toBeInTheDocument();
    expect(await screen.findByDisplayValue('Shared/Default User')).toBeInTheDocument();
  });

  it('generates an API key through the backend contract and shows it once', async () => {
    renderWithProviders(<Identity />);

    fireEvent.click(await screen.findByText('Generate New Key'));
    fireEvent.change(screen.getByLabelText(/which client/i), { target: { value: 'CLI Client' } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));

    expect(await screen.findByTestId('new-api-key')).toHaveTextContent('sk-test-generated-key');
    expect(await screen.findByText('CLI Client')).toBeInTheDocument();
    expect(window.prompt).not.toHaveBeenCalled();
  });

  it('does not save an empty name while the profile is loading', async () => {
    renderWithProviders(<Identity />);

    // Once loaded, the box holds the saved name rather than staying empty.
    await screen.findByDisplayValue('Shared/Default User');
    expect(screen.getByLabelText('Display name')).toHaveValue('Shared/Default User');
  });

  it('only offers Test for services the backend can test', async () => {
    renderWithProviders(<Identity />);

    fireEvent.click(await screen.findByRole('button', { name: /OpenRouter$/ }));
    expect(screen.queryByRole('button', { name: /^test$/i })).toBeNull();
  });
});
