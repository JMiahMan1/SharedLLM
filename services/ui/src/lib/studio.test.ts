import { describe, expect, it } from 'vitest';
import {
  diagnosisLine,
  previewState,
  runSummary,
  stackLine,
  stepsFor,
  stlEditLine,
  stlSizeLine,
  stlVerdict,
  verifyLine,
  type StlSummary,
  type StudioProfile,
} from './studio';

function profile(over: Partial<StudioProfile> = {}): StudioProfile {
  return {
    stack: 'node-vite',
    label: 'Node (Vite)',
    language: 'javascript',
    entry: 'package.json',
    preview_port: 5173,
    notes: [],
    steps: {
      install: { name: 'install', command: 'npm install --no-audit --no-fund', detail: '' },
      test: { name: 'test', command: 'npm test', detail: '' },
      build: { name: 'build', command: 'npm run build', detail: '' },
    },
    ...over,
  };
}

describe('the studio panel helpers', () => {
  it('offers the steps the stack has, in the order a project needs them', () => {
    const steps = stepsFor(profile());

    expect(steps.map((step) => step.name)).toEqual(['install', 'test', 'build']);
    expect(steps[1].command).toBe('npm test');
  });

  it('leaves out a step the stack does not have', () => {
    const python = profile({
      stack: 'python',
      steps: { test: { name: 'test', command: 'python -m pytest -q', detail: '' } },
    });

    expect(stepsFor(python).map((step) => step.name)).toEqual(['test']);
  });

  it('shows nothing before the profile has loaded', () => {
    expect(stepsFor(undefined)).toEqual([]);
    expect(stackLine(undefined)).toBe('detecting…');
  });

  it('names the stack and its preview port', () => {
    expect(stackLine(profile())).toBe('Node (Vite) · preview on 5173');
    expect(stackLine(profile({ stack: 'unknown', label: 'A workspace of files', preview_port: null }))).toBe(
      'no project detected',
    );
  });

  it('reads a finished step as outcome first, command second', () => {
    const base = {
      action: 'test',
      stack: 'node',
      command: 'npm test',
      returncode: 0,
      duration_s: 2.34,
      stdout: '',
      stderr: '',
      message: '',
    };

    expect(runSummary({ ...base, ok: true })).toBe('test passed (exit 0) in 2.3s');
    expect(runSummary({ ...base, ok: false, returncode: 1 })).toBe('test failed (exit 1) in 2.3s');
    expect(runSummary(null)).toBe('');
  });

  it('knows when a preview is live, and says why when it is not', () => {
    expect(previewState({ ok: true, url: 'http://host:14173' })).toEqual({
      live: true,
      label: 'http://host:14173',
    });
    expect(previewState({ ok: false, message: 'nothing answered at the published port' })).toEqual({
      live: false,
      label: 'nothing answered at the published port',
    });
    expect(previewState(null)).toEqual({ live: false, label: '' });
  });
});

function stl(over: Partial<StlSummary> = {}): StlSummary {
  return {
    path: 'parts/bracket.stl',
    format: 'binary',
    triangles: 4,
    vertices: 4,
    size: { x: 1, y: 1, z: 1 },
    longest: 1,
    volume: 0.1667,
    area: 2.366,
    watertight: true,
    open_edges: 0,
    degenerate: 0,
    units: 'likely millimetres',
    warnings: [],
    ...over,
  };
}

describe('the shell check', () => {
  it('says the shell is healthy in one line', () => {
    expect(diagnosisLine({ workspace_id: 'w', ok: true, failed: [], hint: '', probes: [] })).toBe(
      'the sandbox shell is healthy',
    );
  });

  it('names what failed and why', () => {
    const line = diagnosisLine({
      workspace_id: 'w',
      ok: false,
      failed: ['home'],
      hint: 'HOME is /home/sharedllm (NOT writable)',
      probes: [],
    });

    expect(line).toContain('home failed');
    expect(line).toContain('NOT writable');
  });

  it('marks each part of a verdict, and never blames tests for a broken shell', () => {
    const broken = verifyLine({
      ok: false,
      stack: 'python',
      label: 'Python',
      shell: { ok: false, failed: ['identity'] },
      program: { ok: true },
      tests: { ok: true, available: true },
      hint: 'the shell could not report its user',
    });

    expect(broken).toContain('FAILED shell');
    expect(broken).toContain('ok program');
    expect(broken).toContain('ok tests');
    expect(verifyLine(null)).toBe('');
  });

  it('says when a stack has no tests instead of pretending one passed', () => {
    const line = verifyLine({
      ok: true,
      stack: 'static',
      label: 'Static site',
      shell: { ok: true, failed: [] },
      program: { ok: true, skipped: true },
      tests: { ok: true, available: false },
      hint: 'there are no tests to run.',
    });

    expect(line).toContain('ok no tests');
    expect(line).toContain('program (skipped)');
  });
});

describe('the 3D model helpers', () => {
  it('quotes both sizes after an edit, and what was applied', () => {
    const edit = {
      source: 'parts/bracket.stl',
      path: 'parts/bracket_edited.stl',
      format: 'binary',
      applied: { fit_longest: 120, centered: true },
      before: stl(),
      after: stl({ size: { x: 120, y: 120, z: 120 }, longest: 120 }),
      warnings: [],
    };

    expect(stlEditLine(edit)).toBe('1 -> 120 longest (fit_longest, centered)');
    expect(stlEditLine(null)).toBe('');
  });

  it('reads a size as width x depth x height, rounded to two places', () => {
    expect(stlSizeLine(stl({ size: { x: 120, y: 80, z: 12.456 } }))).toBe('120 x 80 x 12.46');
    expect(stlSizeLine(null)).toBe('');
  });

  it('reports a clean solid, or every warning when there is one', () => {
    expect(stlVerdict(stl())).toContain('closed solid');
    const warned = stlVerdict(stl({ warnings: ['the surface is open', '1 triangles have no area'] }));
    expect(warned).toBe('the surface is open; 1 triangles have no area');
    expect(stlVerdict(null)).toBe('');
  });
});
