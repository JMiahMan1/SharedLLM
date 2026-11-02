import { useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { readMealPlan, saveMealPlan } from '../../lib/mealPlanStore';
import { emptyPlan, type PlanDay, type WeeklyPlan } from '../../lib/mealPlan';

/**
 * The week plan (each person's own `Recipes/Meal plan.md`, or the Admin
 * account's when an admin sends as Admin), shared by the Week tab and the
 * recipe card's "Add to the week". Every change is saved as it is made.
 */
export function usePlan(asUser?: 'admin') {
  const queryClient = useQueryClient();
  const key = ['meal-plan', asUser ?? 'me'];
  const query = useQuery({ queryKey: key, queryFn: () => readMealPlan(asUser), staleTime: 60_000, retry: false });
  const plan: WeeklyPlan = query.data?.plan ?? emptyPlan();

  const save = useCallback(
    async (next: WeeklyPlan): Promise<boolean> => {
      const previous = queryClient.getQueryData(key);
      const exists = Boolean((previous as { exists?: boolean } | undefined)?.exists);
      queryClient.setQueryData(key, { plan: next, exists: true });
      try {
        const saved = await saveMealPlan(next, exists, asUser);
        if (saved.response.status !== 'SUCCESS') throw new Error(saved.response.message || 'not saved');
        // The Health card reads the caller's own plan under this key.
        if (!asUser) void queryClient.invalidateQueries({ queryKey: ['meal-plan'], exact: true });
        return true;
      } catch (error) {
        queryClient.setQueryData(key, previous);
        toast.error(error instanceof Error && error.message !== 'not saved' ? error.message : 'Could not save the week plan');
        return false;
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [asUser, queryClient],
  );

  const setDay = useCallback(
    (day: PlanDay, title: string | null) => save({ ...plan, [day]: { ...plan[day], title, cooked: title ? plan[day].cooked && plan[day].title === title : false } }),
    [plan, save],
  );

  return { plan, loading: query.isLoading, error: query.isError, save, setDay };
}
