import { useCallback, useEffect, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { CheckSquare, CloudOff, ShoppingCart, Sparkles, Square } from 'lucide-react';
import toast from 'react-hot-toast';
import OfflineMode, { type CachedItem, type OfflineRequest } from '../../plugins/offlineMode';
import { errorText } from '../../lib/errorText';
import { asOf, localAnswer, locationRows, provisionalShopping, shoppingRows } from '../../lib/offlineAnswers';
import { useOfflineMode } from '../../hooks/useOfflineMode';

const STATUS_WORDS: Record<OfflineRequest['status'], string> = {
  answered: 'Answered',
  expired: 'Expired before a family phone could reach the Voice Assistant',
  sending: 'Sending',
  waiting_for_online_phone: 'Waiting for a family phone that is online',
  no_family_phone_nearby: 'No family phone nearby yet; kept until one is',
};

/**
 * Offline mode (Android app; Settings > Nearby & offline > Offline mode):
 * Voice Assistant questions and quick commands
 * queued for a family phone that is online (answered from this phone's saved
 * information when it can), the household's latest information with how old
 * it is, and shopping edits shown until the server confirms them. A parent's
 * offline lock and unlock for a child are in Guardian (OfflineGuardControls).
 */
export default function OfflineModePanel() {
  const android = Capacitor.getPlatform() === 'android';
  const { offline } = useOfflineMode();
  const [cache, setCache] = useState<CachedItem[]>([]);
  const [requests, setRequests] = useState<OfflineRequest[]>([]);
  const [peerNearby, setPeerNearby] = useState(false);
  const [question, setQuestion] = useState('');
  const [localReply, setLocalReply] = useState<string | null>(null);
  const [newItem, setNewItem] = useState('');
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    Promise.all([OfflineMode.cache(), OfflineMode.requests()])
      .then(([c, r]) => {
        setCache(c.items);
        setRequests(r.requests);
        setPeerNearby(r.peerNearby);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!android) return undefined;
    reload();
    const t = window.setInterval(reload, 15_000);
    return () => window.clearInterval(t);
  }, [android, reload]);

  if (!android) return null;

  const item = (key: string) => cache.find((c) => c.key === key);
  const queue = async (kind: OfflineRequest['kind'], body: Record<string, unknown>) => {
    setBusy(true);
    try {
      await OfflineMode.queue({ kind, body });
      reload();
      return true;
    } catch (e) {
      toast.error(errorText(e, 'Could not queue that'));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const ask = async () => {
    const q = question.trim();
    if (!q) return;
    const local = localAnswer(q, cache);
    if (local) {
      setLocalReply(local);
      setQuestion('');
      return;
    }
    setLocalReply(null);
    const kind = /^(turn|switch|set|start|stop|lock|unlock|open|close|dim)\b/i.test(q) ? 'command' : 'ask';
    if (await queue(kind, { text: q })) setQuestion('');
  };

  const edits = requests
    .filter((r) => r.kind === 'shopping_add' || r.kind === 'shopping_check')
    .map((r) => ({ kind: r.kind, item: String(r.body?.item ?? ''), status: r.status }));
  const shopping = provisionalShopping(shoppingRows(item('shopping')), edits);
  const asks = requests.filter((r) => r.kind === 'ask' || r.kind === 'command' || r.kind === 'location');

  return (
    <div className="space-y-4" data-testid="offline-mode">
      <div className="space-y-1">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <CloudOff size={15} className="text-sky-300" /> Offline mode
        </h3>
        <p className="text-xs text-slate-300" data-testid="offline-mode-status">
          {offline
            ? peerNearby
              ? 'Offline, using nearby family phones.'
              : 'Offline, and no family phone is nearby yet. Everything is kept until one is.'
            : 'Online. This phone keeps the household information fresh for family phones that are not.'}
        </p>
      </div>

      <section className="space-y-2" aria-label="Ask the Voice Assistant offline">
        <h4 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
          <Sparkles size={12} /> Ask the Voice Assistant
        </h4>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void ask(); }}>
          <label htmlFor="offline-ask" className="sr-only">Question or command for the Voice Assistant</label>
          <input id="offline-ask" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={1000}
            placeholder="Where is...? Turn off the lights..." className="glass-input min-h-11 min-w-0 flex-1 px-3 text-sm" />
          <button type="submit" disabled={busy || !question.trim()} className="min-h-11 rounded-lg bg-purple-600 px-3 text-xs font-semibold text-white disabled:opacity-50">
            Ask
          </button>
        </form>
        {localReply && <p className="rounded-lg bg-white/5 p-2 text-xs text-slate-200" data-testid="offline-local-answer">{localReply}</p>}
        {asks.length > 0 && (
          <ul className="space-y-1.5" data-testid="offline-requests">
            {asks.map((r) => (
              <li key={r.id} className="rounded-lg bg-white/5 p-2 text-xs">
                <p className="break-words text-slate-200">{String(r.body?.text ?? (r.kind === 'location' ? 'Share my location' : ''))}</p>
                <p className={r.status === 'answered' ? 'text-emerald-200' : 'text-slate-400'}>
                  {r.status === 'answered' ? r.answer : STATUS_WORDS[r.status]}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-1.5" aria-label="Family">
        <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
          Family {item('locations') && <span className="normal-case tracking-normal text-slate-500">({asOf(item('locations')?.version)})</span>}
        </h4>
        {item('locations') ? (
          <ul className="space-y-1 text-xs text-slate-300" data-testid="offline-locations">
            {locationRows(item('locations')?.data).slice(0, 12).map((r, i) => (
              <li key={i} className="break-words">
                {String(r.display_name ?? r.name ?? r.username ?? r.user ?? '')}: {String(r.zone ?? r.place ?? r.location_name ?? (typeof (r.latitude ?? r.lat) === 'number' ? `${Number(r.latitude ?? r.lat).toFixed(4)}, ${Number(r.longitude ?? r.lon).toFixed(4)}` : 'unknown'))}
              </li>
            ))}
          </ul>
        ) : <p className="text-xs text-slate-500">Nothing saved yet; it arrives from the server or a family phone.</p>}
      </section>

      <section className="space-y-1.5" aria-label="Shopping list">
        <h4 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
          <ShoppingCart size={12} /> Shopping list {item('shopping') && <span className="normal-case tracking-normal">({asOf(item('shopping')?.version)})</span>}
        </h4>
        <ul className="space-y-1" data-testid="offline-shopping">
          {shopping.map((row) => (
            <li key={row.text}>
              <button type="button" disabled={busy || row.checked}
                onClick={() => void queue('shopping_check', { item: row.text })}
                className="flex min-h-11 w-full items-center gap-2 rounded-lg px-2 text-left text-sm text-slate-200 hover:bg-white/5 disabled:cursor-default">
                {row.checked ? <CheckSquare size={15} aria-hidden /> : <Square size={15} aria-hidden />}
                <span className={`min-w-0 flex-1 break-words ${row.checked ? 'line-through text-slate-500' : ''}`}>{row.text}</span>
                {row.provisional && <span className="shrink-0 text-[10px] text-amber-200">Not confirmed yet</span>}
              </button>
            </li>
          ))}
        </ul>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (newItem.trim()) void queue('shopping_add', { item: newItem.trim() }).then((ok) => ok && setNewItem('')); }}>
          <label htmlFor="offline-shopping-add" className="sr-only">Add to the shopping list</label>
          <input id="offline-shopping-add" value={newItem} onChange={(e) => setNewItem(e.target.value)} maxLength={120}
            placeholder="Add an item" className="glass-input min-h-11 min-w-0 flex-1 px-3 text-sm" />
          <button type="submit" disabled={busy || !newItem.trim()} className="min-h-11 rounded-lg bg-white/10 px-3 text-xs font-semibold text-sky-200 disabled:opacity-50">Add</button>
        </form>
      </section>

      {(['calendar', 'reminders', 'bible'] as const).map((key) => item(key) && (
        <details key={key} className="rounded-lg bg-white/5 p-2 text-xs text-slate-300" data-testid={`offline-${key}`}>
          <summary className="min-h-11 cursor-pointer content-center font-semibold text-slate-200">
            {{ calendar: "Today's calendar", reminders: 'Timers and reminders', bible: "Today's Bible reading" }[key]} ({asOf(item(key)?.version)})
          </summary>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words text-[11px]">{JSON.stringify(item(key)?.data, null, 1).slice(0, 4000)}</pre>
        </details>
      ))}

    </div>
  );
}
