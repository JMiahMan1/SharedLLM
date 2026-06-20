import { useQuery } from '@tanstack/react-query';

export function useWidgetData<T>(
  queryKey: string[],
  queryFn: () => Promise<T>,
  refetchInterval?: number
) {
  const { data, error, isLoading, refetch } = useQuery<T, Error>({
    queryKey,
    queryFn,
    refetchInterval,
    retry: 2,
    refetchOnWindowFocus: false,
  });

  return {
    data,
    error: error ? error.message : null,
    isLoading,
    refetch,
  };
}
