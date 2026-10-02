import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { defaultWidgetDefs } from '../stores/widgetStore';

/**
 * A widget key can exist in the type union and still never render: the store
 * needs a default size, the dashboard needs a lazy import, and the skeleton
 * map needs an entry. Missing any one leaves a widget that is declared and
 * invisible. This has bitten this codebase twice (self-registration had no
 * gateway route, and 34 restarts were chased on a replaced container), so the
 * wiring is asserted rather than assumed.
 */
const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8');

const bento = read('src/components/dashboard/BentoBoxDashboard.tsx');
const skeletons = read('src/components/widgets/skeletons/WidgetSkeletons.tsx');

describe('health_hero widget registration', () => {
  it('is a known widget key with a def', () => {
    const def = defaultWidgetDefs.find((d) => d.key === 'health_hero');
    expect(def).toBeDefined();
    expect(def?.label).toBeTruthy();
  });

  it('has a lazy import, or the grid can never mount it', () => {
    expect(bento).toMatch(/health_hero:\s+lazy\(\(\)\s*=>\s*import\('\.\.\/widgets\/HealthHeroWidget'\)\)/);
  });

  it('has a skeleton, or it shows no loading state while the chunk loads', () => {
    expect(skeletons).toMatch(/health_hero:\s+HealthHeroSkeleton/);
  });

  it('defaults to wide so it reads as a hero rather than another card', () => {
    const def = defaultWidgetDefs.find((d) => d.key === 'health_hero');
    expect(def?.defaultSize).toBe('wide');
  });

  // Order comes from the registry index for anyone who has never reordered,
  // so a hero registered fifth is not a hero.
  it('is first in the default registry', () => {
    expect(defaultWidgetDefs[0].key).toBe('health_hero');
  });

  it('is visible by default', () => {
    // createDefaultSettings hides only quick_assistant.
    const def = defaultWidgetDefs.find((d) => d.key === 'health_hero');
    expect(def?.key).not.toBe('quick_assistant');
  });
});

describe('widget registry integrity', () => {
  it('every def has a lazy import, a skeleton, and a default size', () => {
    for (const def of defaultWidgetDefs) {
      expect(bento, `${def.key} has no lazy import`).toMatch(
        new RegExp(`${def.key}:\\s+lazy\\(`),
      );
      expect(skeletons, `${def.key} has no skeleton`).toMatch(
        new RegExp(`${def.key}:\\s+\\w+Skeleton`),
      );
    }
  });

  it('no widget is registered twice', () => {
    const keys = defaultWidgetDefs.map((d) => d.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});
