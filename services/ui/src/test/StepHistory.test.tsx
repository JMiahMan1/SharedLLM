import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import StepHistoryChart from '../components/health/StepHistoryChart';
import StepRangeSelector from '../components/health/StepRangeSelector';
import type { StepRangeBucket } from '../types/api';

const bucket = (over: Partial<StepRangeBucket> = {}): StepRangeBucket => ({
  label: 'Mon',
  start: '2026-10-01',
  end: '2026-10-01',
  steps: 5000,
  days_missing: 0,
  days_recorded: 1,
  complete: true,
  ...over,
});

describe('StepHistoryChart', () => {
  it('draws a bar per day', () => {
    render(<StepHistoryChart buckets={[bucket({ start: '2026-10-01' }), bucket({ start: '2026-10-02' })]} />);
    expect(screen.getByTestId('bar-2026-10-01')).toBeInTheDocument();
    expect(screen.getByTestId('bar-2026-10-02')).toBeInTheDocument();
  });

  // The distinction the whole component exists for: a day we never heard from
  // is not a day with zero steps.
  it('distinguishes a day with no reading from a day with zero steps', () => {
    render(
      <StepHistoryChart
        buckets={[
          bucket({ start: '2026-10-01', steps: 0, days_missing: 0 }),
          bucket({ start: '2026-10-02', steps: 0, days_missing: 1, days_recorded: 0 }),
        ]}
      />,
    );
    expect(screen.getByTestId('bar-2026-10-01')).toHaveAttribute('data-gap', 'false');
    expect(screen.getByTestId('bar-2026-10-02')).toHaveAttribute('data-gap', 'true');
  });

  it('does not describe a missing day as zero steps', () => {
    render(
      <StepHistoryChart
        buckets={[
          bucket({ start: '2026-10-01' }),
          bucket({ start: '2026-10-02', steps: 0, days_missing: 1, days_recorded: 0 }),
        ]}
      />,
    );
    const described = screen
      .getAllByRole('listitem')
      .find((li) => /no reading/i.test(li.textContent ?? ''));
    expect(described).toBeDefined();
    // A missing day must not be announced as zero steps.
    expect(described).not.toHaveTextContent(/\d+ steps/);
  });

  it('says how many days have no reading', () => {
    render(
      <StepHistoryChart
        buckets={[
          bucket({ start: '2026-10-01', days_missing: 1, days_recorded: 0 }),
          bucket({ start: '2026-10-02', days_missing: 1, days_recorded: 0 }),
          bucket({ start: '2026-10-03' }),
        ]}
      />,
    );
    expect(screen.getByTestId('chart-gap-note')).toHaveTextContent('2 days have no reading');
  });

  it('says so when every day has a reading', () => {
    render(<StepHistoryChart buckets={[bucket({ start: '2026-10-01' }), bucket({ start: '2026-10-02' })]} />);
    expect(screen.getByText('Every day has a reading')).toBeInTheDocument();
  });

  it('keeps a small day visible next to a huge one', () => {
    render(
      <StepHistoryChart
        buckets={[bucket({ start: '2026-10-01', steps: 30000 }), bucket({ start: '2026-10-02', steps: 120 })]}
      />,
    );
    const small = screen.getByTestId('bar-2026-10-02').firstElementChild as HTMLElement;
    expect(parseFloat(small.style.height)).toBeGreaterThanOrEqual(4);
  });

  it('marks today separately, since it is still counting', () => {
    render(<StepHistoryChart buckets={[bucket({ start: '2026-10-01' }), bucket({ start: '2026-10-02' })]} partial={1200} />);
    expect(screen.getByTestId('bar-partial')).toBeInTheDocument();
  });

  it('renders nothing for a single day, which is a number not a trend', () => {
    // One bucket stretched across the full width reads as a solid block.
    const { container } = render(<StepHistoryChart buckets={[bucket()]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when there are no buckets', () => {
    const { container } = render(<StepHistoryChart buckets={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('StepRangeSelector', () => {
  it('offers every range on a phone too', () => {
    // Hiding 3M/Y on a small screen would hide data, not defer it.
    render(<StepRangeSelector value="W" onChange={vi.fn()} narrow />);
    for (const id of ['D', 'W', 'M', '3M', 'Y']) {
      expect(screen.getByTestId(`range-${id}`)).toBeInTheDocument();
    }
  });

  it('puts the common ranges first on a phone', () => {
    render(<StepRangeSelector value="W" onChange={vi.fn()} narrow />);
    const order = screen.getAllByRole('button').map((b) => b.dataset.testid);
    expect(order.slice(0, 3)).toEqual(['range-D', 'range-W', 'range-M']);
  });

  it('marks the current range as pressed', () => {
    render(<StepRangeSelector value="M" onChange={vi.fn()} />);
    expect(screen.getByTestId('range-M')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('range-D')).toHaveAttribute('aria-pressed', 'false');
  });

  it('reports the chosen range', async () => {
    const onChange = vi.fn();
    render(<StepRangeSelector value="W" onChange={onChange} />);
    await userEvent.click(screen.getByTestId('range-Y'));
    expect(onChange).toHaveBeenCalledWith('Y');
  });

  it('keeps the full label available even though the face is abbreviated', () => {
    render(<StepRangeSelector value="W" onChange={vi.fn()} />);
    expect(screen.getByTestId('range-3M')).toHaveTextContent('3 Months');
  });
});
