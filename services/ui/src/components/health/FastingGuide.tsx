import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, ChevronDown, Clock, HandHeart, Library, Loader2, ScrollText } from 'lucide-react';
import { api } from '../../services/api';
import type { FastKind, FastSource, FastingTraditions } from '../../types/api';

/** A reference that opens to its text from the family's Bible. */
function Verse({ reference, open: startOpen = false }: { reference: string; open?: boolean }) {
  const [open, setOpen] = useState(startOpen);
  const passage = useQuery({
    queryKey: ['bible-passage', reference],
    queryFn: () => api.getBiblePassage(reference),
    enabled: open,
    staleTime: Infinity,
  });
  const text = passage.data?.verses.map((v) => v.text).join(' ');
  return (
    <li className="rounded-lg bg-white/[0.03]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center justify-between gap-2 px-3 text-left text-xs font-semibold text-amber-100"
      >
        {reference}
        <ChevronDown size={13} className={`shrink-0 text-slate-500 transition ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {open && (
        <div className="px-3 pb-3 text-[13px] leading-relaxed text-slate-200">
          {passage.isLoading ? (
            <Loader2 size={13} className="animate-spin text-slate-500" />
          ) : text ? (
            <>
              <p>{text}</p>
              <Link to={`/bible?ref=${encodeURIComponent(reference)}`} className="mt-1 inline-block text-[11px] text-purple-300 hover:underline">
                Read in the Bible{passage.data?.version ? ` (${passage.data.version})` : ''}
              </Link>
            </>
          ) : (
            <p className="text-slate-500">The verse could not be read from the Bible right now.</p>
          )}
        </div>
      )}
    </li>
  );
}

function Sources({ items }: { items: FastSource[] }) {
  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={item.cite} className="text-[13px] leading-relaxed text-slate-300">
          <span className="font-semibold text-slate-100">{item.cite}.</span> {item.note}
        </li>
      ))}
    </ul>
  );
}

function Section({ icon, title, children, open = false, testId }: { icon: React.ReactNode; title: string; children: React.ReactNode; open?: boolean; testId?: string }) {
  return (
    <details open={open} className="group rounded-xl border border-white/5 bg-black/10" data-testid={testId}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 text-xs font-semibold uppercase tracking-wider text-slate-300">
        {icon}
        <span className="flex-1">{title}</span>
        <ChevronDown size={13} className="text-slate-500 transition group-open:rotate-180" aria-hidden />
      </summary>
      <div className="space-y-2 px-3 pb-3">{children}</div>
    </details>
  );
}

/**
 * What to read and pray while fasting, from Scripture and the early church:
 * the kind's own rule and sources, why the Church fasts, the heart the fast
 * is for, the psalms, the Fathers, the hours of prayer, practical helps, and
 * the family library's books on fasting.
 */
export default function FastingGuide({ traditions, kind, active }: { traditions: FastingTraditions; kind: FastKind | null; active: boolean }) {
  const resources = useQuery({
    queryKey: ['resources', 'fasting'],
    queryFn: () => api.getResources('fasting'),
    staleTime: 10 * 60_000,
    retry: false,
  });
  const books = resources.data?.books ?? [];
  const documents = resources.data?.documents ?? [];
  const [question, setQuestion] = useState('');
  const [asked, setAsked] = useState('');
  const answers = useQuery({
    queryKey: ['resources', 'fasting', asked],
    queryFn: () => api.getResources('fasting', asked),
    enabled: Boolean(asked),
    staleTime: 10 * 60_000,
  });
  return (
    <div className="mt-3 space-y-2" data-testid="fasting-guide">
      <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-3">
        <p className="text-[10px] uppercase tracking-wider text-amber-300/80">Today's verse</p>
        <ul>
          <Verse reference={traditions.today} open />
        </ul>
      </div>

      {kind && kind.key !== 'custom' && (
        <Section icon={<ScrollText size={13} className="text-amber-300" />} title={kind.name} open={active} testId="fasting-kind-guide">
          <p className="text-[13px] leading-relaxed text-slate-200">{kind.summary}</p>
          {kind.rule ? <p className="text-[13px] text-slate-300"><span className="font-semibold text-slate-100">The rule:</span> {kind.rule}</p> : null}
          {kind.caution ? <p className="text-[12px] text-amber-200">{kind.caution}</p> : null}
          <Sources items={kind.sources} />
          <ul className="space-y-1">{kind.scripture.map((ref) => <Verse key={ref} reference={ref} />)}</ul>
        </Section>
      )}

      <Section icon={<BookOpen size={13} className="text-amber-300" />} title="Why we fast">
        <ul className="space-y-1">{traditions.why.map((ref) => <Verse key={ref} reference={ref} />)}</ul>
      </Section>
      <Section icon={<HandHeart size={13} className="text-amber-300" />} title="The heart of the fast">
        <p className="text-[13px] text-slate-400">A fast is for God, in secret, joined to mercy. Not to be seen, and not a bargain.</p>
        <ul className="space-y-1">{traditions.heart.map((ref) => <Verse key={ref} reference={ref} />)}</ul>
      </Section>
      <Section icon={<BookOpen size={13} className="text-amber-300" />} title="Psalms for fasting">
        <ul className="space-y-1">{traditions.psalms.map((ref) => <Verse key={ref} reference={ref} />)}</ul>
      </Section>
      <Section icon={<ScrollText size={13} className="text-amber-300" />} title="From the early church">
        <Sources items={traditions.fathers} />
      </Section>
      <Section icon={<Clock size={13} className="text-amber-300" />} title="The hours of prayer">
        <ul className="space-y-2">
          {traditions.prayer_hours.map((hour) => (
            <li key={hour.clock} className="text-[13px] text-slate-300">
              <span className="font-semibold text-slate-100">{hour.name}</span> ({new Date(`2000-01-01T${hour.clock}`).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}). {hour.reason}
            </li>
          ))}
          <li className="text-[13px] text-slate-400">Pray the Lord's Prayer three times a day (Didache 8.3).</li>
        </ul>
      </Section>
      <Section icon={<HandHeart size={13} className="text-amber-300" />} title="Helps while fasting">
        <ul className="list-disc space-y-1 pl-4 text-[13px] text-slate-300">
          {traditions.helps.map((help) => <li key={help}>{help}</li>)}
        </ul>
      </Section>
      {books.length + documents.length > 0 && (
        <Section icon={<Library size={13} className="text-amber-300" />} title="From our library" testId="fasting-library">
          <ul className="space-y-1.5">
            {books.map((book) => (
              <li key={`${book.title}-${book.author}`} className="text-[13px] text-slate-300">
                <span className="font-semibold text-slate-100">{book.title}</span>
                {book.author ? <span className="text-slate-400"> · {book.author}</span> : null}
              </li>
            ))}
            {documents.map((doc) => (
              <li key={doc.path} className="text-[13px] text-slate-300">{doc.name}</li>
            ))}
          </ul>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setAsked(question.trim());
            }}
            className="flex gap-2"
          >
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ask these books, e.g. why fast on Friday?"
              aria-label="Ask the library about fasting"
              className="glass-input min-h-11 min-w-0 flex-1 px-3 text-base sm:text-sm"
            />
            <button type="submit" className="glass-button min-h-11 px-3 text-xs">Ask</button>
          </form>
          {answers.isFetching ? <Loader2 size={13} className="animate-spin text-slate-500" /> : null}
          {answers.data?.passages.length === 0 && asked ? <p className="text-[12px] text-slate-500">Nothing in these books answers that.</p> : null}
          <ul className="space-y-2" data-testid="fasting-library-passages">
            {answers.data?.passages.map((passage, index) => (
              <li key={index} className="rounded-lg bg-white/[0.03] p-2 text-[12px] leading-relaxed text-slate-300">
                <p className="line-clamp-6">{passage.text}</p>
                <p className="mt-1 text-[11px] text-slate-500">
                  {passage.title}
                  {passage.author ? ` · ${passage.author}` : ''}
                  {passage.chapter ? ` · ${passage.chapter}` : ''}
                </p>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
