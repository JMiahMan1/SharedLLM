import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';

/**
 * Run axe-core against the current page and fail the test on any
 * serious/critical violation. Minor/moderate issues are reported in the
 * test output but do not fail the run.
 */
export async function expectNoA11yViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  const failures = results.violations.filter(
    (violation) => violation.impact === 'serious' || violation.impact === 'critical',
  );
  if (failures.length === 0) return;
  const details = failures
    .map((violation) => {
      const nodes = violation.nodes
        .map((node) => `      ${node.target.join(' ')}: ${node.failureSummary ?? ''}`.trimEnd())
        .join('\n');
      return `- [${violation.impact}] ${violation.id}: ${violation.help} (${violation.nodes.length} node(s))\n${nodes}`;
    })
    .join('\n');
  throw new Error(`Accessibility violations found:\n${details}`);
}
