import type { SimHarness, SimProblem, SimScope } from '../types/api';

export type SimScopeFilter = 'all' | SimScope;
export type SimHarnessFilter = 'all' | SimHarness;

/** Apply the card's own filters, so toggling one never waits on the network. */
export function filterSimProblems(
  problems: SimProblem[],
  filters: { scope: SimScopeFilter; harness: SimHarnessFilter; showRetired: boolean },
): SimProblem[] {
  return problems.filter((problem) => {
    if (filters.scope !== 'all' && problem.scope !== filters.scope) return false;
    if (filters.harness !== 'all' && problem.harness !== filters.harness) return false;
    if (!filters.showRetired && problem.status === 'retired') return false;
    return true;
  });
}

export interface SimSummary {
  total: number;
  portable: number;
  system: number;
  user: number;
  retired: number;
}

/** Counts for the summary pills, from the unfiltered ledger. */
export function simSummary(problems: SimProblem[]): SimSummary {
  return problems.reduce<SimSummary>(
    (acc, problem) => {
      acc.total += 1;
      if (problem.status === 'portable') acc.portable += 1;
      if (problem.scope === 'system') acc.system += 1;
      if (problem.scope === 'user') acc.user += 1;
      if (problem.status === 'retired') acc.retired += 1;
      return acc;
    },
    { total: 0, portable: 0, system: 0, user: 0, retired: 0 },
  );
}
