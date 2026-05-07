import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import Workspaces from './Workspaces';
import { renderWithProviders } from '../test/render';

describe('Workspaces page', () => {
  it('shows copy-paste webhook setup values in the workspace modal', async () => {
    renderWithProviders(<Workspaces />);

    fireEvent.click(await screen.findByText('Add Repository'));

    fireEvent.change(screen.getByPlaceholderText('project-id'), { target: { value: 'demo-workspace' } });
    fireEvent.click(screen.getByRole('button', { name: 'Toggle automated sync' }));

    expect(await screen.findByText('Webhook Setup')).toBeInTheDocument();
    expect(screen.getByText('Payload URL')).toBeInTheDocument();
    expect(screen.getByText('Secret Token')).toBeInTheDocument();
    expect(screen.getByText('GitHub')).toBeInTheDocument();
    expect(screen.getByText('GitLab')).toBeInTheDocument();
    expect(screen.getByText(/demo-workspace/)).toBeInTheDocument();
  });
});
