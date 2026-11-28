import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MissionTimeline } from './MissionTimeline';
import type { MissionLogEvent } from '../../lib/missionLog';

const events: MissionLogEvent[] = [
  { type: 'reasoning', data: 'Plan the work.' },
  { type: 'action', data: 'Executing Tool: DelegateSubagentRequest' },
  { type: 'action_payload', data: '{"task":"Explore the repository"}' },
  { type: 'result_success', data: 'Explorer found three files.' },
  { type: 'action', data: 'Executing Tool: WorkspaceFileWriteRequest' },
  { type: 'action_payload', data: '{"path":"src/a.ts"}' },
  { type: 'result_success', data: 'Post-write lint clean' },
  { type: 'action', data: 'Executing Tool: WorkspaceLintRequest' },
  { type: 'result_error', data: 'LINT FAILED for src/a.ts: missing import' },
  { type: 'action', data: 'Executing Tool: ParallelReadRequest' },
  { type: 'result_success', data: 'Read 3 files in parallel' },
  { type: 'sim', data: 'SIM lesson promoted: read lint output' },
  { type: 'observation_mask', data: 'Observation masked 12 old messages' },
];

describe('MissionTimeline', () => {
  it('draws handoffs, lint verdicts, parallel batches, and telemetry as readable steps', () => {
    render(<MissionTimeline events={events} />);

    expect(screen.getByText('Plan the work.')).toBeInTheDocument();

    const steps = screen.getAllByTestId('mission-step');
    const subagent = steps.find((step) => step.dataset.kind === 'subagent') as HTMLElement;
    expect(subagent).toHaveTextContent('Explorer subagent');
    expect(subagent).toHaveTextContent('Explorer found three files.');
    expect(within(subagent).getByTestId('mission-status')).toHaveTextContent('Handed off');

    const lintPass = steps.find((step) => step.dataset.kind === 'lint' && step.dataset.status === 'success') as HTMLElement;
    expect(lintPass).toHaveTextContent('Lint verification');
    expect(within(lintPass).getByTestId('mission-status')).toHaveTextContent('Verified');

    const lintFail = steps.find((step) => step.dataset.kind === 'lint' && step.dataset.status === 'error') as HTMLElement;
    expect(lintFail).toHaveTextContent('LINT FAILED for src/a.ts');
    expect(within(lintFail).getByTestId('mission-status')).toHaveTextContent('Lint failed');

    const parallel = steps.find((step) => step.dataset.kind === 'parallel') as HTMLElement;
    expect(parallel).toHaveTextContent('Parallel read batch');
    expect(within(parallel).getByTestId('mission-status')).toHaveTextContent('Batch complete');

    const pills = screen.getAllByTestId('mission-telemetry');
    expect(pills).toHaveLength(2);
    expect(pills[0]).toHaveTextContent('SIM');
    expect(pills[0]).toHaveTextContent('SIM lesson promoted');
    expect(pills[1]).toHaveTextContent('Observation masked');
    expect(pills[1]).toHaveTextContent('12 old messages');
  });

  it('hides remembered payloads unless the audit view asks for them', () => {
    const { rerender } = render(<MissionTimeline events={events} />);
    expect(screen.queryByText('{"task":"Explore the repository"}')).not.toBeInTheDocument();

    rerender(<MissionTimeline events={events} showPayloads />);
    expect(screen.getByText('{"task":"Explore the repository"}')).toBeInTheDocument();
  });

  it('still renders an old log with unknown event types', () => {
    render(
      <MissionTimeline
        events={[
          { type: 'system', data: 'plain old log' },
          { type: 'action', data: 'Something happened without a tool name' },
        ]}
      />,
    );

    expect(screen.getByText('plain old log')).toBeInTheDocument();
    expect(screen.getByText('Something happened without a tool name')).toBeInTheDocument();
    expect(screen.getByTestId('mission-step')).toHaveTextContent('Tool');
  });

  it('keeps the empty state quiet', () => {
    render(<MissionTimeline events={[]} emptyText="Nothing ran." />);
    expect(screen.getByText('Nothing ran.')).toBeInTheDocument();
  });
});
