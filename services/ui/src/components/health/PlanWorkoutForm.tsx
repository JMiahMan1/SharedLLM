import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CalendarPlus, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { localDay } from '../../lib/healthHub';
import { errorText } from '../../lib/errorText';
import { WORKOUT_KINDS, workoutEventSummary, type WorkoutKind } from '../../lib/workoutPlan';

/**
 * Put a workout on the calendar (Phase 8: "workout plans get real slots").
 * Goes through the same calendar add the Calendar page uses, so it lands in the
 * person's default calendar and its reminders.
 */
export default function PlanWorkoutForm() {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<WorkoutKind>('walk');
  const [day, setDay] = useState(localDay());
  const [clock, setClock] = useState('');
  const [minutes, setMinutes] = useState(30);

  const add = useMutation({
    mutationFn: async () => {
      const res = await api.addCalendarEvent({ summary: workoutEventSummary(kind, minutes), start_time: `${day} ${clock}` });
      if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'The calendar did not take it');
      return res;
    },
    onSuccess: () => {
      toast.success('Workout added to your calendar');
      setClock('');
      void queryClient.invalidateQueries({ queryKey: ['calendar-app'] });
    },
    onError: (error) => toast.error(errorText(error, 'Could not add the workout')),
  });

  return (
    <form
      className="mt-3 space-y-2 border-t border-white/5 pt-3"
      data-testid="plan-workout"
      onSubmit={(event) => {
        event.preventDefault();
        if (!clock) {
          toast.error('Pick a time');
          return;
        }
        add.mutate();
      }}
    >
      <p className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
        <CalendarPlus size={12} className="text-sky-300" /> Plan a workout
      </p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <select value={kind} onChange={(event) => setKind(event.target.value as WorkoutKind)} aria-label="Workout" className="glass-input min-h-11 px-2 text-base sm:text-sm">
          {WORKOUT_KINDS.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.label}
            </option>
          ))}
        </select>
        <select value={minutes} onChange={(event) => setMinutes(Number(event.target.value))} aria-label="Minutes" className="glass-input min-h-11 px-2 text-base sm:text-sm">
          {[10, 15, 20, 30, 45, 60, 90].map((value) => (
            <option key={value} value={value}>
              {value} min
            </option>
          ))}
        </select>
        <input type="date" value={day} min={localDay()} onChange={(event) => setDay(event.target.value)} aria-label="Workout day" className="glass-input min-h-11 px-2 text-base sm:text-sm" />
        <input type="time" value={clock} onChange={(event) => setClock(event.target.value)} aria-label="Workout time" className="glass-input min-h-11 px-2 text-base sm:text-sm" />
      </div>
      <button type="submit" disabled={add.isPending} className="glass-button flex min-h-11 w-full items-center justify-center gap-2 text-xs font-semibold disabled:opacity-50">
        {add.isPending ? <Loader2 size={13} className="animate-spin" /> : <CalendarPlus size={13} />}
        Add to calendar
      </button>
    </form>
  );
}
