import { useMemo, useState } from 'react';
import { BookOpen, ChevronLeft, ChevronRight } from 'lucide-react';
import { localDay } from '../../lib/healthHub';
import { LESSONS, TOPIC_LABELS, lessonIndexFor } from '../../lib/healthLessons';

/**
 * One micro-lesson at a time (Phase 6). Today's card first; the arrows step
 * through the rest of the library without leaving the card.
 */
export default function MicroLessonCard() {
  const today = useMemo(() => lessonIndexFor(localDay()), []);
  const [offset, setOffset] = useState(0);
  const index = (((today + offset) % LESSONS.length) + LESSONS.length) % LESSONS.length;
  const lesson = LESSONS[index];

  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="lesson-heading" data-testid="micro-lesson">
      <div className="flex items-center justify-between gap-2">
        <h3 id="lesson-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          <BookOpen size={13} className="text-sky-300" />
          {offset === 0 ? "Today's lesson" : 'Lesson'} · {TOPIC_LABELS[lesson.topic]}
        </h3>
        <span className="text-[10px] tabular-nums text-slate-500">
          {index + 1} of {LESSONS.length}
        </span>
      </div>
      <p className="mt-2 text-sm font-semibold text-white" data-testid="lesson-title">
        {lesson.title}
      </p>
      <p className="mt-1 text-xs leading-relaxed text-slate-300">{lesson.body}</p>
      <p className="mt-2 rounded-xl border border-sky-500/20 bg-sky-500/[0.06] p-2.5 text-xs text-sky-100">
        <span className="font-semibold">Try today:</span> {lesson.tryToday}
      </p>
      <div className="mt-3 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setOffset((value) => value - 1)}
          aria-label="Previous lesson"
          className="glass-button flex min-h-11 min-w-11 items-center justify-center px-0"
        >
          <ChevronLeft size={16} />
        </button>
        {offset !== 0 && (
          <button type="button" onClick={() => setOffset(0)} className="text-[11px] text-purple-300 underline underline-offset-2">
            Back to today&apos;s
          </button>
        )}
        <button
          type="button"
          onClick={() => setOffset((value) => value + 1)}
          aria-label="Next lesson"
          className="glass-button flex min-h-11 min-w-11 items-center justify-center px-0"
        >
          <ChevronRight size={16} />
        </button>
      </div>
      <p className="mt-2 text-[10px] text-slate-600">General wellbeing ideas, not medical advice.</p>
    </section>
  );
}
