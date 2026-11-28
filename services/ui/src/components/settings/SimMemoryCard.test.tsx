import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import toast from 'react-hot-toast';
import SimMemoryCard from './SimMemoryCard';
import { api } from '../../services/api';
import { renderWithProviders } from '../../test/render';
import { server } from '../../test/setup';
import type { SimProblem } from '../../types/api';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  loading: vi.fn(),
  dismiss: vi.fn(),
}));

vi.mock('react-hot-toast', () => ({ default: toastMock, toast: toastMock }));

const baseProblem: SimProblem = {
  problem_id: 'p-1',
  scope: 'system',
  user_id: 'default',
  harness: 'raven',
  category: 'lint',
  signature: 'ignored lint',
  rationale: 'The lint result was assumed.',
  edit_or_rule: 'Always read the lint result before claiming success.',
  predicted_outcome: 'Clean write',
  observed_outcome: 'Lint failed',
  outcome_status: 'failure',
  status: 'portable',
  requires_admin: false,
  tags: ['lint', 'verification'],
  verified_models: ['qwen3:8b'],
  regressed_models: ['llama3:8b'],
  model: 'qwen3:8b',
  model_family: 'qwen3',
  retrieval_count: 4,
  updated_at: 1715000000,
};

const problems: SimProblem[] = [
  { ...baseProblem, requires_admin: true },
  {
    ...baseProblem,
    problem_id: 'p-2',
    scope: 'user',
    harness: 'workspace',
    status: 'verified',
    edit_or_rule: 'Read the file before patching it.',
    verified_models: [],
    regressed_models: [],
    tags: [],
  },
  {
    ...baseProblem,
    problem_id: 'p-3',
    scope: 'user',
    harness: 'satellite',
    status: 'candidate',
    edit_or_rule: 'Keep voice answers short.',
    verified_models: [],
    regressed_models: [],
    retrieval_count: 0,
  },
  {
    ...baseProblem,
    problem_id: 'p-4',
    scope: 'system',
    harness: 'workspace',
    status: 'retired',
    edit_or_rule: 'An old rule that regressed another model.',
    verified_models: [],
    regressed_models: ['gemma3:4b'],
  },
];

const answerProblems = () =>
  HttpResponse.json({ status: 'SUCCESS', count: problems.length, problems });

const rowFor = (text: string) => screen.getByText(text).closest('[data-testid="sim-problem"]') as HTMLElement;

describe('SimMemoryCard', () => {
  beforeEach(() => {
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    server.use(http.get('/api/trainer/sim/problems', answerProblems));
  });

  it('summarises the whole ledger and hides retired lessons by default', async () => {
    renderWithProviders(<SimMemoryCard />);

    await screen.findByText(baseProblem.edit_or_rule);
    expect(screen.getAllByTestId('sim-problem')).toHaveLength(3);
    expect(screen.getByTestId('sim-summary-total-lessons')).toHaveTextContent('4');
    expect(screen.getByTestId('sim-summary-portable')).toHaveTextContent('1');
    expect(screen.getByTestId('sim-summary-system')).toHaveTextContent('2');
    expect(screen.getByTestId('sim-summary-personal')).toHaveTextContent('2');
    expect(screen.getByTestId('sim-summary-retired')).toHaveTextContent('1');

    const portable = rowFor(baseProblem.edit_or_rule);
    expect(within(portable).getByTestId('sim-scope-badge')).toHaveTextContent('System');
    expect(within(portable).getByTestId('sim-harness-badge')).toHaveTextContent('Raven');
    expect(within(portable).getByTestId('sim-status-badge')).toHaveTextContent('Portable');
    expect(within(portable).getByTestId('sim-model-chips')).toHaveTextContent('✓ qwen3:8b');
    expect(within(portable).getByTestId('sim-model-chips')).toHaveTextContent('↓ llama3:8b');
    expect(within(portable).getByText('Admins only')).toBeInTheDocument();

    const voice = rowFor('Keep voice answers short.');
    expect(within(voice).getByTestId('sim-harness-badge')).toHaveTextContent('Voice & Chat');
    expect(within(voice).getByTestId('sim-status-badge')).toHaveTextContent('Candidate');
    expect(screen.queryByText('An old rule that regressed another model.')).not.toBeInTheDocument();
  });

  it('filters by scope, harness, and show retired without another request', async () => {
    const user = userEvent.setup();
    let reads = 0;
    server.use(
      http.get('/api/trainer/sim/problems', () => {
        reads += 1;
        return answerProblems();
      }),
    );
    renderWithProviders(<SimMemoryCard />);
    await screen.findByText(baseProblem.edit_or_rule);

    await user.selectOptions(screen.getByLabelText('SIM scope'), 'user');
    expect(screen.getAllByTestId('sim-problem')).toHaveLength(2);

    await user.selectOptions(screen.getByLabelText('SIM harness'), 'satellite');
    expect(screen.getAllByTestId('sim-problem')).toHaveLength(1);
    expect(screen.getByText('Keep voice answers short.')).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('SIM scope'), 'all');
    await user.selectOptions(screen.getByLabelText('SIM harness'), 'all');
    expect(screen.queryByText('An old rule that regressed another model.')).not.toBeInTheDocument();

    await user.click(screen.getByLabelText('Show retired'));
    expect(screen.getAllByTestId('sim-problem')).toHaveLength(4);
    expect(within(rowFor('An old rule that regressed another model.')).getByTestId('sim-status-badge')).toHaveTextContent('Retired');
    expect(reads).toBe(1);
  });

  it('lets an admin consolidate and refreshes the ledger', async () => {
    const user = userEvent.setup();
    let reads = 0;
    let posts = 0;
    server.use(
      http.get('/api/trainer/sim/problems', () => {
        reads += 1;
        return answerProblems();
      }),
      http.post('/api/trainer/sim/consolidate', () => {
        posts += 1;
        return HttpResponse.json({
          status: 'SUCCESS',
          checked: 8,
          updated: 5,
          promoted: ['p-1', 'p-2'],
          model_specific: ['p-3'],
          retired: ['p-4'],
        });
      }),
    );
    renderWithProviders(<SimMemoryCard isAdmin />);
    await screen.findByText(baseProblem.edit_or_rule);

    await user.click(screen.getByTestId('sim-consolidate'));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('5 updated')));
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('2 portable'));
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('1 retired'));
    expect(posts).toBe(1);
    await waitFor(() => expect(reads).toBeGreaterThanOrEqual(2));
  });

  it('keeps a non-admin read-only', async () => {
    renderWithProviders(<SimMemoryCard />);
    await screen.findByText(baseProblem.edit_or_rule);
    expect(screen.queryByTestId('sim-consolidate')).not.toBeInTheDocument();
  });

  it('shows a calm error when the endpoint is unavailable', async () => {
    // Mock the client method rather than the transport: the shared axios
    // layer intentionally retries 503s with backoff, which would outlast the
    // assertion window. The component's handling is what this test covers.
    vi.spyOn(api, 'trainerSimProblems').mockRejectedValueOnce({
      response: { status: 503, data: { detail: 'Trainer is restarting, try again shortly.' } },
    });
    renderWithProviders(<SimMemoryCard />);

    const error = await screen.findByTestId('sim-error');
    expect(error).toHaveTextContent('SIM memory is unavailable right now.');
    expect(error).toHaveTextContent('Trainer is restarting, try again shortly.');
    expect(within(error).getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });
});
