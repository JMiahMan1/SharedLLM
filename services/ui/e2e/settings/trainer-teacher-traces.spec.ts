/**
 * The Lab's Training tab: the teacher-traces card, hermetic. Every API call is
 * mocked and nothing reaches a server. Runs in every hermetic project, so it is
 * checked on a desktop, phones and tablets.
 *
 * The mocked trainer answers like the real one: a dropped trace carries no goal
 * (the scrubber removed it), only verified work counts towards lessons, and the
 * score history is the local model's pass rate on teacher tasks, run by run.
 */
import { expect, test, type Page, type Route } from '@playwright/test';
import { seedAuth } from '../fixtures/mediaMocks';
import { expectNoHorizontalOverflow } from '../fixtures/layoutChecks';

const TRACES = [
  {
    id: 'tt-aaaaaaaaaaaa', source: 'opencode', model: 'openrouter/teacher-ultra', model_location: 'cloud',
    started_at: '2026-11-20T10:00:00Z', imported_at: '2026-11-21T08:00:00Z',
    goal: 'Fix the off-by-one in the pager so the last page shows', outcome: 'success',
    outcome_evidence: ['last check passed: pytest services/gateway/tests/test_pager.py -q'],
    step_count: 5, tools: ['read', 'edit', 'shell'], areas: ['services/gateway'], scrub_status: 'masked',
    scrub_masked: 4, scrub_dropped_steps: 0, drop_reason: null, lesson_id: null, case_id: 'teacher-aaaaaaaaaaaa',
  },
  {
    id: 'tt-bbbbbbbbbbbb', source: 'cline', model: 'cline/teacher-flash', model_location: 'cloud',
    started_at: '2026-11-19T10:00:00Z', imported_at: '2026-11-21T08:00:00Z',
    goal: 'Make the page size configurable with a very long description that has to wrap on a small phone screen without pushing anything sideways',
    outcome: 'failure', outcome_evidence: ['the next message corrected this task'], step_count: 3, tools: ['editor'],
    areas: ['services/gateway'], scrub_status: 'clean', scrub_masked: 0, scrub_dropped_steps: 0, drop_reason: null,
    lesson_id: null, case_id: null,
  },
  {
    id: 'tt-cccccccccccc', source: 'opencode', model: 'openrouter/teacher-ultra', model_location: 'cloud',
    started_at: '2026-11-21T09:00:00Z', imported_at: '2026-11-21T09:30:00Z', goal: '', outcome: 'unverified',
    outcome_evidence: [], step_count: 12, tools: [], areas: [], scrub_status: 'dropped', scrub_masked: 0,
    scrub_dropped_steps: 6, drop_reason: 'session_touched_secrets', lesson_id: null, case_id: null,
  },
];

const SUMMARY = {
  traces: {
    total: 3, by_status: { masked: 1, clean: 1, dropped: 1 }, by_outcome: { success: 1, failure: 1, unverified: 1 },
    by_source: { opencode: 2, cline: 1 }, by_location: { cloud: 3 }, by_model: {}, masked_values: 4, dropped_steps: 6,
  },
  lessons: { written: 0, last_written_at: null, eligible: 1 },
  eval_cases: 1,
  scores: [
    { report_id: 'eval-1', started_at: '2026-11-01T00:00:00Z', model: 'local-moe', cases: 10, passed: 3, pass_rate: 0.3 },
    { report_id: 'eval-2', started_at: '2026-11-15T00:00:00Z', model: 'local-moe', cases: 10, passed: 5, pass_rate: 0.5 },
  ],
  exports: [],
  scrub_version: '1',
  health: {
    last_pass_at: '2026-11-21T10:00:00Z',
    sync: { at: '2026-11-21T09:30:00Z', ok: false, command: 'sync', queue_length: 2, pending_sessions: 0, tasks: 3, sent: 0,
      error: 'the gateway answered 502' },
    lessons: { at: '2026-11-21T10:00:00Z', written: 1, failed: 0, total: 4 },
    eval: { at: '2026-11-21T10:00:00Z', cases: 10, passed: 5, pass_rate: 0.5 },
    snapshot: null,
    failures: [{ part: 'sync', label: 'Teacher sync from the Mac', reason: 'the last sync on the Mac failed: the gateway answered 502',
      since: '2026-11-21T09:30:00Z' }],
  },
};

async function mockTrainer(page: Page) {
  const posts: Array<{ path: string; body: unknown }> = [];
  const traceQueries: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await seedAuth(page);
  await page.route((u: URL) => u.pathname.startsWith('/api/') || u.pathname.startsWith('/execute/') || u.pathname === '/health',
    async (route: Route) => {
      const url = new URL(route.request().url());
      const p = url.pathname;
      const method = route.request().method();
      if (p === '/api/users/me') return route.fallback();
      if (method !== 'GET') {
        posts.push({ path: p, body: route.request().postDataJSON() });
        if (p === '/api/trainer/teacher/lessons') {
          return route.fulfill({ json: { written: [{ id: 'tt-aaaaaaaaaaaa', lesson_id: 'lesson-teacher-1', rule: 'For a task like...' }], skipped: [], failed: [], dry_run: false } });
        }
        if (p === '/api/trainer/teacher/dataset') {
          return route.fulfill({ json: { format: 'openai', paths: { train: '/data/trainer/datasets/teacher-openai-train.jsonl', eval: '/data/trainer/datasets/teacher-openai-eval.jsonl' }, train_rows: 1, eval_rows: 0, skipped: {}, created_at: '20261121T100000Z' } });
        }
        return route.fulfill({ json: { status: 'SUCCESS' } });
      }
      if (p === '/api/trainer/teacher') return route.fulfill({ json: SUMMARY });
      if (p === '/api/trainer/teacher/traces') {
        traceQueries.push(url.search);
        const outcome = url.searchParams.get('outcome');
        const status = url.searchParams.get('status');
        const rows = TRACES.filter((t) => (!outcome || t.outcome === outcome) && (!status || t.scrub_status === status));
        return route.fulfill({ json: { traces: rows } });
      }
      if (p === '/api/trainer/catalog') {
        return route.fulfill({ json: { generated_at: '2026-11-21T00:00:00Z', fingerprint: 'x', features: [],
          counts: { total: 3, by_service: { gateway: 3 }, by_kind: { route: 2, tool: 1 }, by_tag: {} } } });
      }
      if (p === '/api/trainer/curriculum') {
        return route.fulfill({ json: { summary: { total: 2, by_bucket: { repair: 1, teacher: 1 }, by_difficulty: {} }, buckets: [], cases: [] } });
      }
      if (p === '/api/trainer/backlog') return route.fulfill({ json: { generated_at: null, eval_report_id: null, proposals: [], lessons: [] } });
      if (p === '/api/trainer/hardware') return route.fulfill({ json: { reachable: true, ready: true, reasons: [] } });
      if (p === '/api/trainer/models') return route.fulfill({ json: { total: 0, models: [], chosen: 'local-moe', trainer_model_setting: '', trainer_model_missing: false } });
      if (p === '/api/trainer/benchmarks') return route.fulfill({ json: { total: 0, chosen_model: '', directories: {}, weak_categories: [], results: [] } });
      if (p === '/api/trainer/reports') return route.fulfill({ json: { reports: [] } });
      if (/\/api\/(health|info)\b/.test(p)) return route.fulfill({ json: { status: 'ok', version: 'e2e', git_sha: 'e2e' } });
      if (/\/api\/(config|calendar\/settings)\b/.test(p)) return route.fulfill({ json: { status: 'SUCCESS', settings: {}, config: {} } });
      if (/\/api\/telemetry\//.test(p)) return route.fulfill({ json: { notifications: [], jobs: [] } });
      return route.fulfill({ json: [] });
    });
  return { posts, traceQueries, errors };
}

async function openCard(page: Page) {
  await page.goto('/lab?tab=training');
  const card = page.getByTestId('teacher-traces');
  await card.scrollIntoViewIfNeeded();
  await expect(card).toBeVisible();
  return card;
}

test.describe('teacher traces in the Training tab', () => {
  test('shows what was kept, what was dropped and how the local model scores', async ({ page }) => {
    const { errors } = await mockTrainer(page);
    const card = await openCard(page);

    await expect(card.getByTestId('teacher-health')).toContainText('2 queued');
    await expect(card.getByTestId('teacher-health')).toContainText('5/10 passed');
    await expect(card.getByTestId('teacher-failures')).toContainText('the gateway answered 502');
    await expect(card.getByTestId('teacher-score')).toContainText('50%');
    await expect(card.getByTestId('teacher-trend')).toContainText('+20 points since the first run');
    await expect(card.getByTestId('teacher-scores').locator('li')).toHaveCount(2);
    const rows = card.getByTestId('teacher-trace');
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText('Fix the off-by-one');
    await expect(rows.nth(0)).toContainText('masked · 4 masked');
    await expect(rows.nth(0)).toContainText('test case');
    const dropped = rows.nth(2);
    await expect(dropped).toContainText('dropped: session touched secrets');
    await expect(dropped).toContainText('content removed by the scrubber');

    await expectNoHorizontalOverflow(page);
    const short = await card.getByRole('button').evaluateAll((els) =>
      els.filter((el) => (el as HTMLElement).offsetParent && el.getBoundingClientRect().height < 43.5)
        .map((el) => (el.textContent || '').trim()));
    expect(short, 'buttons shorter than 44px').toEqual([]);
    expect(errors).toEqual([]);
  });

  test('filters by outcome and scrub status', async ({ page }) => {
    const { traceQueries } = await mockTrainer(page);
    const card = await openCard(page);

    await card.getByRole('tab', { name: 'Verified', exact: true }).click();
    await expect(card.getByTestId('teacher-trace')).toHaveCount(1);
    await card.getByRole('tab', { name: 'Dropped' }).click();
    await expect(card.getByTestId('teacher-trace')).toHaveCount(1);
    await expect(card.getByTestId('teacher-trace')).toContainText('dropped');
    expect(traceQueries.some((q) => q.includes('outcome=success'))).toBe(true);
    expect(traceQueries.some((q) => q.includes('status=dropped'))).toBe(true);
  });

  test('writes lessons and exports the dataset', async ({ page }) => {
    const { posts } = await mockTrainer(page);
    const card = await openCard(page);

    await card.getByRole('button', { name: /Write lessons \(1 ready\)/ }).click();
    await expect.poll(() => posts.map((p) => p.path)).toContain('/api/trainer/teacher/lessons');

    await card.getByLabel('Format').selectOption('sharegpt');
    await card.getByRole('button', { name: /Export dataset/ }).click();
    await expect(card.getByTestId('teacher-export')).toContainText('1 train / 0 eval rows');
    const exported = posts.find((p) => p.path === '/api/trainer/teacher/dataset');
    expect(exported?.body).toEqual({ format: 'sharegpt', eval_share: 0.1 });
    await expectNoHorizontalOverflow(page);
  });
});
