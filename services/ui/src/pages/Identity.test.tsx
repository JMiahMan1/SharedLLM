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

  it('generates an API key through the backend contract', async () => {
    renderWithProviders(<Identity />);

    fireEvent.click(await screen.findByText('Generate New Key'));

    expect(await screen.findByText('CLI Client')).toBeInTheDocument();
  });
});
