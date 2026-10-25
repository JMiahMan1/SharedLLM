import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Loader2, Mic, Search, Square, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { foodEntryFromSearch, suggestedGrams } from '../../lib/foodEntry';
import { splitSpokenFoods } from '../../lib/voiceFood';
import { useAudioRecorder } from '../../hooks/useAudioRecorder';
import type { FoodEntry, FoodSearchItem } from '../../types/api';

interface DraftRow {
  heard: string;
  query: string;
  servings: string;
  matches: FoodSearchItem[];
  /** Index into `matches`, or -1 for "leave this one out". */
  pick: number;
  grams: string;
}

/**
 * Voice food logging (Phase 4 quick add): say what you ate, check the draft,
 * then log. The recording goes through the same speech-to-text the assistant
 * uses (`/api/stt/transcribe`); the transcript stays editable, each food is
 * looked up in the food database, and nothing is saved until "Log" -- every
 * match, portion and count can be changed or dropped first.
 */
export default function VoiceFoodDraft({
  day,
  meal,
  kid,
  onLogged,
}: {
  day: string;
  meal: FoodEntry['meal'];
  kid: boolean;
  onLogged: () => void;
}) {
  const [transcript, setTranscript] = useState('');
  const [rows, setRows] = useState<DraftRow[] | null>(null);

  const transcribe = useMutation({
    mutationFn: (blob: Blob) => api.transcribeAudio(blob),
    onSuccess: (result) => {
      const text = (result.transcript || '').trim();
      if (!text) toast('Nothing was heard. Try again, or type it below.');
      setTranscript(text);
      setRows(null);
    },
    onError: (error) => toast.error(errorText(error, 'Speech could not be turned into text. Type it instead.')),
  });
  const { recording, start, stop } = useAudioRecorder({
    onRecorded: (blob) => transcribe.mutate(blob),
    onError: (reason) =>
      toast.error(
        reason === 'denied'
          ? 'Microphone permission is needed to record.'
          : reason === 'empty'
            ? 'Nothing was recorded. Check the microphone and try again.'
            : 'Recording is not available here. Type what you ate instead.',
      ),
  });

  const lookUp = useMutation({
    mutationFn: async (text: string) => {
      const items = splitSpokenFoods(text);
      if (items.length === 0) throw new Error('No foods found in that. Try "two eggs and toast".');
      return Promise.all(
        items.map(async (item) => {
          const found = await api.searchFood(item.query, 5);
          const usable = found.results.filter((result) => result.per_100g.calories !== null);
          const grams = usable[0] ? suggestedGrams(usable[0]) : null;
          return {
            heard: item.heard,
            query: item.query,
            servings: String(item.servings),
            matches: usable,
            pick: usable.length ? 0 : -1,
            grams: grams ? String(grams) : '',
          } satisfies DraftRow;
        }),
      );
    },
    onSuccess: setRows,
    onError: (error) => toast.error(errorText(error, 'Could not look those foods up')),
  });

  const log = useMutation({
    mutationFn: async (chosen: DraftRow[]) => {
      for (const row of chosen) {
        await api.addFood(
          foodEntryFromSearch(row.matches[row.pick], {
            grams: Number(row.grams),
            servings: Number(row.servings) || 1,
            day,
            meal,
            source: 'voice',
          }),
        );
      }
      return chosen.length;
    },
    onSuccess: (count) => {
      toast.success(`Logged ${count} ${count === 1 ? 'food' : 'foods'}`);
      onLogged();
    },
    onError: (error) => toast.error(errorText(error, 'Could not log those foods')),
  });

  const update = (index: number, patch: Partial<DraftRow>) =>
    setRows((current) => current?.map((row, at) => (at === index ? { ...row, ...patch } : row)) ?? null);

  const chosen = (rows ?? []).filter((row) => row.pick >= 0);
  const incomplete = chosen.some((row) => !(Number(row.grams) > 0));

  return (
    <div className="mt-3 space-y-3" data-testid="voice-food">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={async () => {
            if (recording) {
              stop();
              return;
            }
            await start();
          }}
          disabled={transcribe.isPending}
          aria-pressed={recording}
          className={`flex min-h-11 items-center gap-2 rounded-xl border px-3 text-xs font-semibold ${
            recording ? 'animate-pulse border-rose-500/40 bg-rose-500/20 text-rose-100' : 'border-white/10 bg-white/[0.03] text-slate-200'
          }`}
        >
          {transcribe.isPending ? <Loader2 size={14} className="animate-spin" /> : recording ? <Square size={14} /> : <Mic size={14} />}
          {recording ? 'Stop' : transcribe.isPending ? 'Listening back…' : 'Say what you ate'}
        </button>
        <span className="text-[11px] text-slate-500">e.g. “two eggs and a slice of toast”</span>
      </div>
      <label className="block text-[11px] text-slate-500">
        What you said (edit freely)
        <textarea
          value={transcript}
          onChange={(event) => setTranscript(event.target.value)}
          rows={2}
          aria-label="What you ate"
          className="glass-input mt-1 w-full px-3 py-2 text-base sm:text-sm"
        />
      </label>
      <button
        type="button"
        onClick={() => lookUp.mutate(transcript)}
        disabled={!transcript.trim() || lookUp.isPending}
        className="glass-button flex min-h-11 items-center gap-2 px-3 text-xs font-semibold disabled:opacity-50"
      >
        {lookUp.isPending ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
        Find these foods
      </button>

      {rows && (
        <div className="space-y-2" data-testid="voice-food-draft">
          <p className="text-[11px] text-amber-200">A draft: check each match and portion. Nothing is saved until you log it.</p>
          {rows.map((row, index) => (
            <div key={`${row.heard}-${index}`} className="rounded-xl border border-white/10 bg-white/[0.03] p-2.5 text-xs" data-testid={`voice-row-${index}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-slate-400">Heard “{row.heard}”</span>
                <button type="button" onClick={() => update(index, { pick: -1 })} aria-label={`Leave out ${row.heard}`} className="flex h-11 w-11 items-center justify-center text-slate-500 hover:text-red-300">
                  <X size={13} />
                </button>
              </div>
              {row.matches.length === 0 ? (
                <p className="text-amber-300">No match for “{row.query}”. Add it with Search or By hand.</p>
              ) : (
                <div className="mt-1 grid grid-cols-1 gap-2 sm:grid-cols-3">
                  <select
                    value={row.pick}
                    onChange={(event) => {
                      const pick = Number(event.target.value);
                      const grams = pick >= 0 ? suggestedGrams(row.matches[pick]) : null;
                      update(index, { pick, grams: grams ? String(grams) : '' });
                    }}
                    aria-label={`Food for ${row.heard}`}
                    className="glass-input min-h-11 px-2 text-base sm:text-sm"
                  >
                    {row.matches.map((match, at) => (
                      <option key={match.code} value={at}>
                        {match.name}
                        {match.brand ? ` · ${match.brand}` : ''}
                      </option>
                    ))}
                    <option value={-1}>Leave this out</option>
                  </select>
                  <label className="text-[11px] text-slate-500">
                    Portion (g)
                    <input
                      type="number"
                      min={1}
                      value={row.grams}
                      onChange={(event) => update(index, { grams: event.target.value })}
                      aria-label={`Grams for ${row.heard}`}
                      className="glass-input mt-1 min-h-11 w-full px-2 text-base sm:text-sm"
                    />
                  </label>
                  <label className="text-[11px] text-slate-500">
                    Servings
                    <input
                      type="number"
                      min={0.1}
                      step={0.1}
                      value={row.servings}
                      onChange={(event) => update(index, { servings: event.target.value })}
                      aria-label={`Servings for ${row.heard}`}
                      className="glass-input mt-1 min-h-11 w-full px-2 text-base sm:text-sm"
                    />
                  </label>
                </div>
              )}
              {!kid && row.pick >= 0 && Number(row.grams) > 0 && row.matches[row.pick].per_100g.calories !== null && (
                <p className="mt-1 text-[10px] text-slate-500">
                  ≈ {Math.round(((row.matches[row.pick].per_100g.calories ?? 0) * Number(row.grams) * (Number(row.servings) || 1)) / 100)} kcal
                </p>
              )}
            </div>
          ))}
          <button
            type="button"
            onClick={() => log.mutate(chosen)}
            disabled={chosen.length === 0 || incomplete || log.isPending}
            data-testid="voice-food-log"
            className="glass-button flex min-h-11 w-full items-center justify-center gap-2 text-xs font-semibold disabled:opacity-50"
          >
            {log.isPending ? <Loader2 size={13} className="animate-spin" /> : null}
            {incomplete ? 'Give every food a portion' : `Log ${chosen.length} ${chosen.length === 1 ? 'food' : 'foods'}`}
          </button>
        </div>
      )}
    </div>
  );
}
