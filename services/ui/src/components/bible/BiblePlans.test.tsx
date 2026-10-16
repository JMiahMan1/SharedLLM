import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BiblePlans from './BiblePlans';
import type { BiblePlanEnrollment } from '../../types/api';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const apiMock = vi.hoisted(() => ({
  getBiblePlans: vi.fn(),
  getBiblePlan: vi.fn(),
  getBiblePlanFamily: vi.fn(),
  startBiblePlan: vi.fn(),
  completeBiblePlanDay: vi.fn(),
  undoBiblePlanDay: vi.fn(),
  catchUpBiblePlan: vi.fn(),
  stopBiblePlan: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

const JOHN: BiblePlanEnrollment = {
  plan_id: 'john-21', name: 'John in 21 Days', started_on: '2026-10-01', finished_on: null,
  days: 21, completed: 2, completed_days: [1, 2], percent: 10, calendar_day: 5, current_day: 5,
  next_day: 3, behind: 2, finished: false,
  today: { day: 5, readings: [{ ref: 'John 5', osis: 'John', chapter_start: 5, chapter_end: 5 }] },
};
const CATALOGUE = [
  { id: 'john-21', name: 'John in 21 Days', blurb: 'A chapter a day', days: 21, category: 'New Testament', chapters: 21 },
  { id: 'proverbs-31', name: 'A Proverb a Day', blurb: 'One chapter', days: 31, category: 'Wisdom', chapters: 31 },
];

function renderIt(onOpenRef = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BiblePlans onOpenRef={onOpenRef} />
    </QueryClientProvider>,
  );
  return onOpenRef;
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getBiblePlans.mockResolvedValue({ catalogue: CATALOGUE, mine: [JOHN] });
  apiMock.getBiblePlanFamily.mockResolvedValue({ plan_id: 'john-21', members: [] });
});

describe('Bible reading plans', () => {
  it("shows today's reading, opens it in the reader and marks the day read", async () => {
    const user = userEvent.setup();
    apiMock.completeBiblePlanDay.mockResolvedValue({ ...JOHN, completed: 3, newly_earned: [] });
    const open = renderIt();
    await user.click(await screen.findByRole('button', { name: /John 5/ }));
    expect(open).toHaveBeenCalledWith('John 5');
    await user.click(screen.getByTestId('bible-plan-complete-john-21'));
    expect(apiMock.completeBiblePlanDay).toHaveBeenCalledWith('john-21', 5);
  });

  it('says how far behind and offers to catch up', async () => {
    const user = userEvent.setup();
    apiMock.catchUpBiblePlan.mockResolvedValue({ ...JOHN, behind: 0 });
    renderIt();
    expect(await screen.findByText('2 days behind.')).toBeInTheDocument();
    await user.click(screen.getByTestId('bible-plan-catchup-john-21'));
    expect(apiMock.catchUpBiblePlan).toHaveBeenCalledWith('john-21');
  });

  it('browses the catalogue by category and starts a plan', async () => {
    const user = userEvent.setup();
    apiMock.startBiblePlan.mockResolvedValue({ ...JOHN, plan_id: 'proverbs-31' });
    renderIt();
    await user.click(await screen.findByTestId('bible-plans-browse'));
    expect(screen.getByText('Wisdom')).toBeInTheDocument();
    expect(screen.getByTestId('bible-plan-start-john-21')).toHaveTextContent('Following');
    await user.click(screen.getByTestId('bible-plan-start-proverbs-31'));
    await waitFor(() => expect(apiMock.startBiblePlan).toHaveBeenCalledWith('proverbs-31'));
  });

  it('announces a badge earned by finishing a day', async () => {
    const user = userEvent.setup();
    apiMock.completeBiblePlanDay.mockResolvedValue({
      ...JOHN,
      newly_earned: [{ id: 'plan_days_7', name: 'A Week in the Word', description: '', points: 5, earned_on: '2026-10-13' }],
    });
    renderIt();
    await user.click(await screen.findByTestId('bible-plan-complete-john-21'));
    expect(await screen.findByRole('status')).toHaveTextContent('New badge: A Week in the Word');
  });
});

describe('reading along', () => {
  it('shows family members on the same plan', async () => {
    apiMock.getBiblePlanFamily.mockResolvedValue({
      plan_id: 'john-21',
      members: [{ username: 'mia', completed: 9, percent: 43, behind: 0, finished: false }],
    });
    renderIt();
    expect(await screen.findByTestId('bible-plan-family-john-21')).toHaveTextContent('mia 43%');
  });
  it('asks before stopping a plan, since starting again begins at day 1', async () => {
    const user = userEvent.setup();
    apiMock.stopBiblePlan.mockResolvedValue({ stopped: 'john-21' });
    renderIt();
    await user.click(await screen.findByTestId('bible-plan-stop-john-21'));
    expect(apiMock.stopBiblePlan).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Keep it' }));
    expect(screen.queryByTestId('bible-plan-stop-confirm-john-21')).not.toBeInTheDocument();
    await user.click(screen.getByTestId('bible-plan-stop-john-21'));
    await user.click(screen.getByTestId('bible-plan-stop-confirm-john-21'));
    expect(apiMock.stopBiblePlan).toHaveBeenCalledWith('john-21');
  });
});
