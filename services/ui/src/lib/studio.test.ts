import { describe, expect, it } from 'vitest';
import { previewState, runSummary, stackLine, stepsFor, type StudioProfile } from './studio';

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
