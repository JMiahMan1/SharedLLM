import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { BookOpen, X } from 'lucide-react';
import { api } from '../../services/api';

const DISMISSED_KEY = 'family-chat-verse-dismissed';
const today = () => new Date().toISOString().slice(0, 10);

function dismissedToday(): boolean {
  try {
    return localStorage.getItem(DISMISSED_KEY) === today();
  } catch {
    return false;
  }
}

/**
 * Today's verse pinned under the chat header, the way a family group pins
 * the thing everyone should see. One tap reads it in the Bible; closing it
 * hides it until tomorrow's verse.
 */
export default function VerseBanner() {
  const navigate = useNavigate();
  const [hidden, setHidden] = useState(dismissedToday);
  const { data } = useQuery({
    queryKey: ['bible-verse-of-day'],
    queryFn: () => api.getBibleVerseOfDay(),
    staleTime: 60 * 60 * 1000,
    retry: false,
    enabled: !hidden,
  });
  const verse = data && 'reference' in data && data.reference ? data : null;
  if (hidden || !verse) return null;
  return (
    <div className="flex shrink-0 items-center gap-2 bg-gradient-to-r from-indigo-900/90 via-purple-900/90 to-fuchsia-900/80 px-3 py-2" data-testid="chat-verse-banner">
      <BookOpen size={15} className="shrink-0 text-amber-200" />
      <p className="min-w-0 flex-1 truncate text-[13px] text-slate-100">
        <span className="font-semibold text-amber-100">{verse.reference}</span>
        <span className="text-slate-300"> — {verse.text}</span>
      </p>
      <button
        type="button"
        onClick={() => navigate(`/bible?ref=${encodeURIComponent(verse.reference)}`)}
        className="shrink-0 rounded-full bg-sky-400 px-3 py-1 text-[12px] font-bold text-slate-950 hover:bg-sky-300"
      >
        Read
      </button>
      <button
        type="button"
        aria-label="Hide today's verse"
        onClick={() => {
          try {
            localStorage.setItem(DISMISSED_KEY, today());
          } catch {
            // Hiding is a convenience; without storage it simply shows again.
          }
          setHidden(true);
        }}
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-300 hover:bg-white/10"
      >
        <X size={14} />
      </button>
    </div>
  );
}
