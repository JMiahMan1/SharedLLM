import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'react-hot-toast';
import { AlertTriangle, Camera, HeartPulse, Trash2, X } from 'lucide-react';
import { api } from '../../services/api';
import type { GardenDiagnosis, GardenPlant, GardenPlantPhoto } from '../../types/api';

const cardClass = 'rounded-xl border border-white/10 bg-white/5 p-4';
const buttonClass =
  'inline-flex items-center justify-center gap-1.5 px-3 py-2 min-h-11 rounded-lg bg-white/5 border border-white/10 text-slate-300 hover:bg-white/10 text-xs transition-colors disabled:opacity-50';
const inputClass =
  'w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-emerald-500 text-sm pointer-coarse:min-h-11';
const labelClass = 'flex flex-col gap-1 text-[10px] text-slate-400';

const URGENCY_STYLE: Record<string, string> = {
  high: 'border-rose-500/40 bg-rose-500/10 text-rose-200',
  medium: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
  low: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
};

/** A camera/file control that hands the chosen photo back (capture= for phones). */
export function PhotoInput({
  id, label, onPick, disabled,
}: {
  id: string;
  label: string;
  onPick: (file: File) => void;
  disabled?: boolean;
}) {
  return (
    <label htmlFor={id} className={`${buttonClass} cursor-pointer ${disabled ? 'opacity-50' : ''}`}>
      <Camera size={14} /> {label}
      <input
        id={id}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onPick(file);
          event.target.value = '';
        }}
      />
    </label>
  );
}

function DiagnosisBody({ diagnosis }: { diagnosis: GardenDiagnosis }) {
  return (
    <div className="space-y-2">
      <div className={`rounded-lg border p-3 ${URGENCY_STYLE[diagnosis.urgency] || URGENCY_STYLE.low}`}>
        <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
          <AlertTriangle size={13} /> {diagnosis.urgency.toUpperCase()} urgency · {diagnosis.confidence} confidence
        </p>
        <p className="text-sm mt-1">{diagnosis.summary}</p>
      </div>
      {diagnosis.likely_causes.length > 0 && (
        <div>
          <h4 className="text-[11px] font-semibold text-slate-300">Likely causes</h4>
          <ul className="mt-1 space-y-1">
            {diagnosis.likely_causes.map((cause) => (
              <li key={cause.cause} className="text-[11px] text-slate-300">
                <span className="text-white">{cause.cause}</span> ({cause.confidence})
                {cause.why ? ` — ${cause.why}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      {diagnosis.recovery_steps.length > 0 && (
        <div>
          <h4 className="text-[11px] font-semibold text-slate-300">What to do, in order</h4>
          <ol className="mt-1 space-y-1 list-decimal list-inside">
            {diagnosis.recovery_steps.map((step) => (
              <li key={step} className="text-[11px] text-slate-300">{step}</li>
            ))}
          </ol>
        </div>
      )}
      {diagnosis.care_adjustments.length > 0 && (
        <div>
          <h4 className="text-[11px] font-semibold text-slate-300">Care changes</h4>
          <ul className="mt-1 space-y-1">
            {diagnosis.care_adjustments.map((step) => (
              <li key={step} className="text-[11px] text-slate-300">· {step}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** The Plant Doctor: a leaf photo in, a grounded diagnosis out. */
export function PlantDoctorModal({ plant, onClose }: { plant: GardenPlant; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [symptoms, setSymptoms] = useState('');
  const [diagnosis, setDiagnosis] = useState<GardenDiagnosis | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const diagnose = useMutation({
    mutationFn: (file: File) => api.gardenDiagnosePlant(plant.id, file, symptoms.trim() || undefined),
    onSuccess: (result) => {
      setDiagnosis(result.diagnosis);
      setWarning(result.nextcloud_warning ?? null);
      void queryClient.invalidateQueries({ queryKey: ['garden-photos', plant.id] });
      toast.success('Diagnosis saved to the photo journal');
    },
    onError: (error: unknown) => {
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || 'The photo could not be diagnosed');
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Plant Doctor"
        className="bg-slate-900 border border-white/20 rounded-2xl p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center mb-2">
          <h2 className="text-xl font-bold text-white inline-flex items-center gap-2">
            <HeartPulse size={18} /> Plant Doctor
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="min-h-11 min-w-11 inline-flex items-center justify-center rounded-lg text-slate-400 hover:text-white">
            <X size={18} />
          </button>
        </div>
        <p className="text-xs text-slate-400 mb-4">
          A close photo of the affected leaves, plus anything you have noticed. The diagnosis is
          kept in this plant&apos;s photo journal.
        </p>
        <label className={labelClass} htmlFor="doctor-symptoms">
          What have you noticed? (optional)
          <textarea
            id="doctor-symptoms"
            className={inputClass}
            rows={2}
            value={symptoms}
            onChange={(e) => setSymptoms(e.target.value)}
            placeholder="Lower leaves yellowing, soil still wet"
          />
        </label>
        <div className="mt-3">
          <PhotoInput
            id="doctor-photo"
            label={diagnose.isPending ? 'Looking at the photo…' : 'Take or choose a leaf photo'}
            onPick={(file) => diagnose.mutate(file)}
            disabled={diagnose.isPending}
          />
        </div>
        {warning && <p className="mt-3 text-[11px] text-amber-200">{warning}</p>}
        {diagnosis && (
          <div className="mt-4">
            <DiagnosisBody diagnosis={diagnosis} />
          </div>
        )}
      </div>
    </div>
  );
}

/** The growth journal: thumbnails, diagnoses and add/delete controls. */
export function PhotoJournalPanel({
  plant, canDeleteAny, currentUser,
}: {
  plant: GardenPlant;
  canDeleteAny: boolean;
  currentUser: string;
}) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);

  const photosQuery = useQuery({
    queryKey: ['garden-photos', plant.id],
    queryFn: () => api.gardenListPlantPhotos(plant.id),
    staleTime: 60_000,
  });
  const photos: GardenPlantPhoto[] = photosQuery.data?.photos ?? [];

  const upload = useMutation({
    mutationFn: (file: File) => api.gardenUploadPlantPhoto(plant.id, file, { kind: 'progress' }),
    onSuccess: (result) => {
      setPending(false);
      if (result.nextcloud_warning) toast(result.nextcloud_warning, { icon: '⚠️' });
      else toast.success('Photo added to the journal');
      void queryClient.invalidateQueries({ queryKey: ['garden-photos', plant.id] });
    },
    onError: () => {
      setPending(false);
      toast.error('The photo could not be added');
    },
  });

  const remove = useMutation({
    mutationFn: (photoId: string) => api.gardenDeletePlantPhoto(plant.id, photoId),
    onSuccess: () => {
      toast.success('Photo removed');
      void queryClient.invalidateQueries({ queryKey: ['garden-photos', plant.id] });
    },
    onError: () => toast.error('Could not remove the photo'),
  });

  return (
    <div className={cardClass}>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wider">Growth Journal</h3>
        <PhotoInput
          id="journal-photo"
          label={upload.isPending ? 'Adding…' : 'Add Progress Photo'}
          onPick={(file) => {
            setPending(true);
            upload.mutate(file);
          }}
          disabled={upload.isPending || pending}
        />
      </div>
      {photos.length === 0 ? (
        <p className="text-xs text-slate-500 italic">
          No photos yet. A monthly progress shot makes it easy to see how a plant is doing.
        </p>
      ) : (
        <ul className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {photos.map((photo) => {
            const mine = (photo.taken_by || '').toLowerCase() === currentUser.toLowerCase();
            return (
              <li key={photo.id} className="rounded-lg border border-white/10 bg-slate-950/40 p-2">
                {photo.thumb_path ? (
                  <img
                    src={api.gardenPlantPhotoThumbUrl(plant.id, photo.id)}
                    alt={`${photo.kind} photo from ${photo.taken_at.slice(0, 10)}`}
                    className="w-full h-28 object-cover rounded-md bg-slate-800"
                    loading="lazy"
                  />
                ) : (
                  <div className="w-full h-28 rounded-md bg-slate-800 flex items-center justify-center text-[10px] text-slate-500">
                    no preview
                  </div>
                )}
                <p className="mt-1 text-[10px] text-slate-400">
                  {new Date(photo.taken_at).toLocaleDateString()}
                  {photo.taken_by ? ` · ${photo.taken_by}` : ''}
                  {photo.kind === 'diagnosis' ? ' · diagnosis' : ''}
                </p>
                {photo.notes && <p className="text-[10px] text-slate-300">{photo.notes}</p>}
                {(canDeleteAny || mine) && (
                  <button
                    type="button"
                    onClick={() => remove.mutate(photo.id)}
                    className={`${buttonClass} mt-1 !text-rose-300`}
                    aria-label={`Delete photo from ${photo.taken_at.slice(0, 10)}`}
                  >
                    <Trash2 size={12} /> Delete
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
