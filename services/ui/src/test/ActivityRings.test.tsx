import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ActivityRings from '../components/health/ActivityRings';

const stepsRing = (over: Record<string, unknown> = {}) => ({
  id: 'steps',
  label: 'Steps',
  actual: 5000,
  goal: 10000,
  unit: 'steps',
  ...over,
});

describe('ActivityRings', () => {
  it('renders one ring per metric', () => {
    render(
      <ActivityRings
        rings={[stepsRing(), { id: 'distance', label: 'Distance', actual: 3, goal: 5, unit: 'mi' }]}
        baseline={8000}
      />,
    );
    expect(screen.getByTestId('ring-steps')).toBeInTheDocument();
    expect(screen.getByTestId('ring-distance')).toBeInTheDocument();
  });

  it('shows the actual value as text, not only as a graphic', () => {
    render(<ActivityRings rings={[stepsRing({ actual: 5000, goal: 10000 })]} baseline={8000} />);
    // A ring alone is unreadable to assistive tech and ambiguous at a glance.
    expect(screen.getByText('5,000')).toBeInTheDocument();
    expect(screen.getByText('5,000 steps to go')).toBeInTheDocument();
  });

  it('states the same fact in the group label as the rings draw', () => {
    render(<ActivityRings rings={[stepsRing({ actual: 5000, goal: 10000 })]} baseline={8000} />);
    expect(screen.getByRole('group')).toHaveAccessibleName('Steps: 5,000 of 10,000 steps');
  });

  it('marks the goal met instead of showing a negative remainder', () => {
    render(<ActivityRings rings={[stepsRing({ actual: 12000, goal: 10000 })]} baseline={8000} />);
    expect(screen.getByText('Goal met')).toBeInTheDocument();
  });

  it('bands the zone against the users own baseline', () => {
    render(<ActivityRings rings={[stepsRing({ actual: 9000 })]} baseline={14000} />);
    expect(screen.getByTestId('ring-zone-steps')).toHaveTextContent('Below usual');
  });

  it('says so when there is too little history to judge', () => {
    render(<ActivityRings rings={[stepsRing()]} baseline={null} thin baselineMinDays={7} />);
    expect(screen.getByTestId('rings-thin')).toHaveTextContent('Needs 7 days of history');
  });

  it('does not invent a day requirement when the server did not send one', () => {
    render(<ActivityRings rings={[stepsRing()]} thin baselineMinDays={null} />);
    expect(screen.getByTestId('rings-thin')).toHaveTextContent('Not enough history yet');
  });

  it('hides the zone verdict while thin, since it would be a guess', () => {
    render(<ActivityRings rings={[stepsRing()]} baseline={null} thin baselineMinDays={7} />);
    expect(screen.getByTestId('ring-zone-steps')).toHaveTextContent('On your usual pace');
  });

  it('falls back to the default goal when the server sent none', () => {
    render(<ActivityRings rings={[stepsRing({ goal: null, actual: 5000 })]} defaultGoal={10000} baseline={8000} />);
    expect(screen.getByText('5,000 steps to go')).toBeInTheDocument();
  });

  it('renders nothing when there is nothing to show', () => {
    const { container } = render(<ActivityRings rings={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
