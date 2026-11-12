import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Database, Loader2 } from 'lucide-react';
import {
  failureText, getStatisticsStatus, importStatistics, statSpanText,
} from '../../lib/dashboards';
import { HEADER_ACTION } from '../ui/PageHeader';

const KEY = ['dashboard', 'statistics-status'];

/**
 * Admins: how much long-term history (hourly statistics) the hub holds, when it
 * was last brought over from the old Home Assistant recorder, and a button to
 * do that again. History and Energy draw their long ranges from these numbers,
 * so "why is last year empty?" is answered here.
 */
export default function StatisticsStatusCard() {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: KEY, queryFn: getStatisticsStatus, retry: false });
  const [busy, setBusy] = useState(false);

  async function runImport() {
    setBusy(true);
    try {
      await importStatistics();
      toast.success('History imported');
      await queryClient.invalidateQueries({ queryKey: KEY });
    } catch (err) {
      toast.error(failureText(err, 'The import did not work'));
    } finally {
      setBusy(false);
    }
  }

  const data = status.data;
  const span = statSpanText(data?.statistics);
  const lastImport = data?.last_import?.imported_at;
  return (
    <section className="glass-card rounded-2xl p-4 space-y-2" aria-label="Long-term history" data-testid="statistics-status">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
        <Database size={16} className="text-sky-400" aria-hidden />Long-term history
      </h2>
      {status.isLoading && <div className="skeleton h-10 w-full rounded-xl" role="status" aria-label="Loading" />}
      {status.isError && <p className="text-sm text-red-300">{failureText(status.error, 'Could not read the history status')}</p>}
      {data && (
        <>
          <p className="text-sm text-slate-300">
            {span ? <>Hourly history for {data.metadata} sensor{data.metadata === 1 ? '' : 's'}, {span}.</> : 'No long-term history yet.'}
          </p>
          <p className="text-xs text-slate-500">
            {lastImport ? `Last brought over from Home Assistant ${new Date(lastImport).toLocaleString()}.` : 'Never imported from Home Assistant.'}
          </p>
          {data.last_error && <p className="text-xs text-amber-200" role="status">Last problem: {data.last_error}</p>}
        </>
      )}
      <button type="button" className={HEADER_ACTION} disabled={busy || status.isLoading} onClick={() => void runImport()}>
        {busy ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null}
        {busy ? 'Importing, this can take a few minutes...' : 'Import history from Home Assistant'}
      </button>
    </section>
  );
}
