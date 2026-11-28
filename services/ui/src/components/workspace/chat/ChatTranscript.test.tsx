import { describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChatTranscript from './ChatTranscript';
import { renderWithProviders } from '../../../test/render';
import type { WorkspaceChatMessage } from '../../../types/api';

vi.mock('../../settings/RavenMissionDetail', () => ({
  default: ({ missionId, onClose }: { missionId: number | null; onClose: () => void }) =>
    missionId == null ? null : (
      <div data-testid="raven-detail">
        Mission {missionId}
        <button type="button" onClick={onClose}>Close</button>
      </div>
    ),
}));

const messages: WorkspaceChatMessage[] = [
  {
    role: 'assistant',
    parts: [
      { type: 'reasoning', text: 'Plan the work.\nCheck the files.', step: 1 },
      {
        type: 'tool',
        id: 'd1',
        name: 'DelegateRavenRequest',
        input: { prompt: 'Fix the lint failure', mission_id: 77 },
        output: 'Raven mission queued.',
        status: 'done',
      },
      {
        type: 'tool',
        id: 'd2',
        name: 'DelegateSubagentRequest',
        input: { task: 'Explore the repository' },
        output: 'Found three candidate files.',
        status: 'done',
      },
      {
        type: 'tool',
        id: 'd3',
        name: 'WorkspaceSearchRequest',
        input: { query: 'mission output' },
        output: '{"status":"SUCCESS","mission_id":91}',
        status: 'done',
      },
      { type: 'text', text: 'The handoffs are running.' },
    ],
  },
];

describe('ChatTranscript harness representation', () => {
  it('folds settled thinking and gives handoffs their own readable cards', async () => {
    renderWithProviders(<ChatTranscript messages={messages} live={null} />);

    const reasoning = screen.getByTestId('reasoning-part');
    expect(reasoning).toHaveTextContent('Thought');
    expect(reasoning).toHaveTextContent('Plan the work.');
    expect(reasoning).not.toHaveTextContent('Check the files.');

    const user = userEvent.setup();
    await user.click(within(reasoning).getByRole('button'));
    expect(reasoning).toHaveTextContent('Check the files.');

    const handoffs = screen.getAllByTestId('delegation-part');
    expect(handoffs).toHaveLength(2);
    expect(handoffs[0]).toHaveTextContent('Raven mission handoff');
    expect(handoffs[0]).toHaveTextContent('Raven Mission');
    expect(handoffs[0]).toHaveTextContent('Fix the lint failure');
    expect(handoffs[1]).toHaveTextContent('Explorer subagent');
    expect(handoffs[1]).toHaveTextContent('Subagent');
    expect(handoffs[1]).toHaveTextContent('Explore the repository');

    expect(screen.getByTestId('tool-part')).toHaveTextContent('Workspace search');
    expect(screen.getAllByTestId('mission-link')).toHaveLength(2);
    expect(screen.getByRole('button', { name: /Open mission #77/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Open mission #91/i })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Open mission #77/i }));
    expect(screen.getByTestId('raven-detail')).toHaveTextContent('Mission 77');
  });

  it('shows live thinking as open while the turn streams', () => {
    renderWithProviders(
      <ChatTranscript
        messages={[]}
        live={{
          parts: [{ type: 'reasoning', text: 'Working it out', step: 1 }],
          liveText: '',
          meta: { resolved_mode: 'raven' },
        }}
      />,
    );

    expect(screen.getByTestId('reasoning-part')).toHaveTextContent('Thinking…');
    expect(screen.getByText('Raven Mission')).toBeInTheDocument();
  });
});
