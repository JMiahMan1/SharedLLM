import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { defaultWidgetDefs } from '../stores/widgetStore';
import type { WidgetKey } from '../types/widget';

// Vitest's cwd is the `services/ui` project root, so anchor on that rather than
// import.meta.url (which is not a file: URL under the test runner).
const SRC = (rel: string) => resolve(process.cwd(), 'src', rel);
const read = (rel: string) => readFileSync(SRC(rel), 'utf8');

const BENTO = read('components/dashboard/BentoBoxDashboard.tsx');
const SKELETONS = read('components/widgets/skeletons/WidgetSkeletons.tsx');
const STORE = read('stores/widgetStore.ts');

/**
 * The dashboard used to carry two health widgets: `health_hero`, whose registry
 * label was "Today", and `health_activity`, labelled "Health". Both fetched step
 * data and both drew a ring, so the same numbers appeared twice under two
 * different names — and "Today" gave no hint that it was a health widget at all.
 *
 * These tests guard the fix. The behavioural one matters most: it fails if
 * anything ever registers a second widget that reads step data, whichever way it
 * is labelled, so the duplicate cannot come back under a new name.
 */
describe('dashboard health widgets', () => {
  it('has exactly one health widget registered', () => {
    const keys = defaultWidgetDefs.map((d) => d.key);
    expect(keys.filter((k) => k.includes('health'))).toEqual(['health_activity']);
  });

  it('has no widget labelled "Today"', () => {
    // "Today" was the label that made the duplicate unrecognisable.
    expect(defaultWidgetDefs.map((d) => d.label)).not.toContain('Today');
  });

  it('has no widget key mentioning health_hero', () => {
    // `defaultSizes` is module-private, so read it from source rather than
    // importing a name that does not exist.
    const sizeBlock = STORE.slice(STORE.indexOf('defaultSizes'));
    const sized = [...sizeBlock.matchAll(/^\s{2}([a-z_]+):\s*'/gm)].map((m) => m[1]);
    expect(sized.filter((k) => k.includes('hero'))).toEqual([]);
    expect(sized.length).toBe(defaultWidgetDefs.length);
  });

  it('leaves no orphaned entries in the lazy map or skeleton map', () => {
    // A key registered in one map but not the others renders nothing, or a
    // skeleton with no widget — both look like a broken dashboard.
    const registered = new Set(defaultWidgetDefs.map((d) => d.key));
    for (const [name, source] of [
      ['BentoBoxDashboard LazyWidgets', BENTO],
      ['WidgetSkeletonSelector', SKELETONS],
    ] as const) {
      const orphans = [...source.matchAll(/^\s{2}([a-z_]+):\s*(lazy|React\.FC|[A-Z])/gm)]
        .map((m) => m[1])
        .filter((k) => !registered.has(k as WidgetKey));
      expect(orphans, `${name} has keys with no registry entry`).toEqual([]);
    }
  });

  it('keeps the baseline insight on the Health page, not a duplicate card', () => {
    // Removing the hero must not orphan heroInsight/ActivityRings: the Health
    // page is where the baseline-relative insight still renders.
    const healthPage = read('pages/Health.tsx');
    expect(healthPage).toMatch(/heroInsight/);
    expect(healthPage).toMatch(/ActivityRings/);
  });

  it('only one widget component fetches step data for the dashboard', () => {
    // The strongest guard: any dashboard widget that calls a step endpoint is a
    // candidate duplicate, so assert exactly one does — whatever it is named.
    const widgetDir = resolve(process.cwd(), 'src', 'components', 'widgets') + '/';
    const registered = new Set(defaultWidgetDefs.map((d) => d.key));
    const stepReaders: string[] = [];
    // Read every file in the widget directory rather than a hardcoded list, so a
    // newly added widget is covered without editing this test.
    for (const file of readdirSync(widgetDir)) {
      if (!file.endsWith('.tsx') || file.endsWith('.test.tsx')) continue;
      const base = file.replace(/\.tsx$/, '');
      // Map the component file back to its registry key via the lazy import map.
      const key = [...BENTO.matchAll(/^\s{2}([a-z_]+):\s*lazy\(\(\)\s*=>\s*import\('\.\.\/widgets\/([^']+)'\)/gm)]
        .find((m) => m[2] === base)?.[1];
      if (!key || !registered.has(key as WidgetKey)) continue;
      if (/getDailySteps|getStepRanges/.test(readFileSync(`${widgetDir}${file}`, 'utf8'))) {
        stepReaders.push(file);
      }
    }
    expect(stepReaders).toEqual(['HealthActivityWidget.tsx']);
  });
});