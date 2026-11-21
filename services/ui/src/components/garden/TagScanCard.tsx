import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { toast } from 'react-hot-toast';
import { Camera, Droplets, MapPin, Scissors, X } from 'lucide-react';
import { api } from '../../services/api';

const cardClass = 'rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4 mb-4';
const buttonClass =
  'inline-flex items-center justify-center gap-1.5 px-3 py-2 min-h-11 rounded-lg border text-xs transition-colors disabled:opacity-50';

/**
 * What a scanned NFC pot sticker or QR code lands on.
 *
 * Putting a phone on a pot is a read: this card only offers the taps that make
 * sense for whatever the tag is bound to. `?tag=` is in the URL, so the same
 * link works from a printed QR code on a bed.
 */
export function TagScanCard({ tagId, onDismiss }: { tagId: string; onDismiss: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const scan = useQuery({
    queryKey: ['garden-tag', tagId],
    queryFn: () => api.gardenScanTag(tagId),
    retry: false,
  });

  const water = useMutation({
    mutationFn: async () => {
      const target = scan.data;
      if (!target) return null;
      if (target.target_kind === 'plant') {
        return api.gardenLogCare(target.target_id, { action: 'water', source: 'nfc_tag' });
      }
      return api.gardenWaterAll(target.target_id);
    },
    onSuccess: () => {
      toast.success('Watered');
      void queryClient.invalidateQueries({ queryKey: ['garden-today'] });
      void queryClient.invalidateQueries({ queryKey: ['garden-plants'] });
    },
    onError: () => toast.error('That watering could not be logged'),
  });

  if (scan.isError) {
    return (
      <div className={cardClass} role="status">
        <div className="flex items-start justify-between gap-2">
          <p className="text-sm text-slate-200">
            This tag is not linked to a plant or bed yet. An adult can link it from a plant&apos;s
            hardware settings.
          </p>
          <button type="button" onClick={onDismiss} aria-label="Dismiss tag" className="min-h-11 min-w-11 inline-flex items-center justify-center text-slate-400 hover:text-white">
            <X size={16} />
          </button>
        </div>
      </div>
    );
  }
  if (!scan.data) return null;

  const target = scan.data;
  const moisture = typeof target.readings?.moisture_pct === 'number' ? target.readings.moisture_pct : null;

  return (
    <div className={cardClass} role="status">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-wider text-emerald-300 inline-flex items-center gap-1">
            <MapPin size={11} /> Scanned tag
          </p>
          <h2 className="text-lg font-semibold text-white truncate">{target.target_name || 'Garden'}</h2>
          <p className="text-xs text-slate-400">
            {target.target_kind === 'plant'
              ? target.plant?.species_ref
              : `${target.location?.plant_count ?? 0} plants in this bed`}
            {target.due_today ? ' · care is due today' : target.next_due_at ? ` · next ${new Date(target.next_due_at).toLocaleDateString()}` : ''}
            {moisture !== null ? ` · soil ${moisture}%` : ''}
          </p>
        </div>
        <button type="button" onClick={onDismiss} aria-label="Dismiss tag" className="min-h-11 min-w-11 inline-flex items-center justify-center text-slate-400 hover:text-white">
          <X size={16} />
        </button>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => water.mutate()}
          disabled={water.isPending}
          className={`${buttonClass} bg-emerald-500/15 border-emerald-500/40 text-emerald-200 hover:bg-emerald-500/25`}
        >
          <Droplets size={12} /> {target.target_kind === 'plant' ? 'Mark watered' : 'Water this bed'}
        </button>
        {target.target_kind === 'plant' && (
          <>
            <button
              type="button"
              onClick={() => navigate(`/garden/plants/${target.target_id}`)}
              className={`${buttonClass} bg-white/5 border-white/10 text-slate-200 hover:bg-white/10`}
            >
              <Scissors size={12} /> Open plant
            </button>
            <button
              type="button"
              onClick={() => navigate(`/garden/plants/${target.target_id}?journal=1`)}
              className={`${buttonClass} bg-white/5 border-white/10 text-slate-200 hover:bg-white/10`}
            >
              <Camera size={12} /> Add photo
            </button>
          </>
        )}
      </div>
    </div>
  );
}
