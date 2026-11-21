import { describe, expect, it } from 'vitest';
import {
  benchmarkLine,
  bucketBars,
  freeVramLabel,
  hardwareVerdict,
  passRateLabel,
  proposalTone,
  summariseCatalog,
  type TrainerBucketResult,
  type TrainerCatalog,
  type TrainerHardware,
} from './trainer';

function bucket(cases: number, passed: number): TrainerBucketResult {
  return { cases, passed, pass_rate: cases ? passed / cases : 0 };
}

function catalog(over: Partial<TrainerCatalog> = {}): TrainerCatalog {
  return {
    generated_at: '2026-01-01T00:00:00Z',
    fingerprint: 'abc',
    counts: {
      total: 10,
      by_service: { gateway: 6, rag: 3, ui: 1 },
      by_kind: { route: 7, tool: 2, page: 1 },
      by_tag: { memory: 5, workspaces: 3, media: 1 },
    },
    features: [],
    ...over,
  };
}

function hardware(over: Partial<TrainerHardware> = {}): TrainerHardware {
  return { reachable: true, ready: true, reasons: [], ...over };
}

describe('the model host card', () => {
  it('says the host is idle with its free slots', () => {
    const verdict = hardwareVerdict(hardware({ slots: { total: 4, available: 3 } }));

    expect(verdict).toEqual({ ready: true, label: '3 of 4 slots free' });
  });

  it('reports why a busy host cannot run a pass', () => {
    const verdict = hardwareVerdict(
      hardware({ ready: false, reasons: ['only 400 MB of GPU memory is free'] }),
    );

    expect(verdict.ready).toBe(false);
    expect(verdict.label).toContain('400 MB');
  });

  it('treats an unreachable host as no host, not as ready', () => {
    const verdict = hardwareVerdict({ reachable: false, ready: false, reasons: ['connection refused'] });

    expect(verdict.ready).toBe(false);
    expect(verdict.label).toBe('connection refused');
  });

  it('asks before it knows', () => {
    expect(hardwareVerdict(undefined).label).toContain('asking');
  });

  it('labels free VRAM in gigabytes, and an unknown number honestly', () => {
    expect(freeVramLabel(hardware({ free_vram_mb: 8192 }))).toBe('8.0 GB free');
    expect(freeVramLabel(hardware({ free_vram_mb: null }))).toBe('unknown');
    expect(freeVramLabel({ reachable: false, ready: false, reasons: [] })).toBe('no host');
  });

  it('summarises the benchmark report card in one line', () => {
    expect(benchmarkLine(undefined)).toBe('no benchmark results yet');
    expect(benchmarkLine({ total: 0, chosen_model: '', directories: {}, weak_categories: [], results: [] })).toBe(
      'no benchmark results yet',
    );
    expect(
      benchmarkLine({
        total: 1,
        chosen_model: 'qwen3:8b',
        directories: {},
        results: [],
        weak_categories: [
          { category: 'coding', score: 20, tests_run: 6, tests_passed: 1 },
          { category: 'reasoning', score: 45, tests_run: 4, tests_passed: 2 },
        ],
      }),
    ).toBe('coding 20/100 · reasoning 45/100');
  });
});

describe('the training tab helpers', () => {
  it('orders buckets worst first, because the failures are what to read', () => {
    const rows = bucketBars({ writing: bucket(4, 4), repair: bucket(2, 0), tools: bucket(4, 1) });

    expect(rows.map((row) => row.bucket)).toEqual(['repair', 'tools', 'writing']);
    expect(rows[0].passRate).toBe(0);
    expect(rows[2].passRate).toBe(100);
  });

  it('treats a missing pass rate as zero rather than NaN', () => {
    const rows = bucketBars({ repair: { cases: 0, passed: 0, pass_rate: 0 } });

    expect(rows[0].passRate).toBe(0);
  });

  it('summarises the catalogue by service, tools and screens', () => {
    const summary = summariseCatalog(catalog());

    expect(summary.total).toBe(10);
    expect(summary.services[0]).toEqual({ service: 'gateway', count: 6 });
    expect(summary.tools).toBe(2);
    expect(summary.pages).toBe(1);
    expect(summary.topTags[0]).toEqual({ tag: 'memory', count: 5 });
  });

  it('survives a catalogue with no kinds or tags', () => {
    const summary = summariseCatalog(
      catalog({
        counts: { total: 0, by_service: {}, by_kind: {}, by_tag: {} },
      }),
    );

    expect(summary).toMatchObject({ total: 0, tools: 0, pages: 0, services: [], topTags: [] });
  });

  it('labels a pass rate as a whole percentage', () => {
    expect(passRateLabel(0)).toBe('0%');
    expect(passRateLabel(0.666)).toBe('67%');
    expect(passRateLabel(1)).toBe('100%');
    expect(passRateLabel(Number.NaN)).toBe('0%');
  });

  it('gives each kind of proposal its own tone', () => {
    expect(proposalTone('model_unavailable')).toContain('rose');
    expect(proposalTone('curriculum_repair')).toContain('amber');
    expect(proposalTone('tool_doc')).toContain('amber');
    expect(proposalTone('lesson_validation')).toContain('indigo');
    expect(proposalTone('ui_gap')).toContain('cyan');
    expect(proposalTone('something_else')).toContain('slate');
  });
});
