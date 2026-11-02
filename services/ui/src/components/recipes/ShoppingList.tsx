import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Loader2, Plus, Send, Share2, ShoppingCart, Trash2 } from 'lucide-react';
import { api } from '../../services/api';
import { parseShoppingList, shoppingShareText, writeShoppingList, type ShoppingItem } from '../../lib/shoppingList';
import { readShoppingList, saveShoppingList } from '../../lib/shoppingListStore';
import { mirrorItems } from '../../lib/skylightMeals';
import type { RecipeShareRequest } from './RecipeView';

/**
 * The week's shopping list: built from the plan on the Week tab, ticked off
 * here while shopping (every tick is saved), added to by hand, and sent to the
 * Skylight grocery list or the family chat.
 */
export default function ShoppingList({
  asUser,
  skylightOff,
  onShare,
  onGoToWeek,
}: {
  asUser?: 'admin';
  skylightOff: boolean;
  onShare: (share: RecipeShareRequest) => void;
  onGoToWeek: () => void;
}) {
  const queryClient = useQueryClient();
  const key = ['shopping-list', asUser ?? 'me'];
  const query = useQuery({ queryKey: key, queryFn: () => readShoppingList(asUser), retry: false });
  const items: ShoppingItem[] = parseShoppingList(query.data?.text ?? '');
  const [adding, setAdding] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const write = async (rows: ShoppingItem[]) => {
    const previous = query.data;
    const text = writeShoppingList(rows);
    queryClient.setQueryData(key, { text, exists: true });
    try {
      const saved = await saveShoppingList(text, Boolean(previous?.exists), asUser);
      if (saved.response.status !== 'SUCCESS') throw new Error(saved.response.message || '');
    } catch (error) {
      queryClient.setQueryData(key, previous);
      toast.error((error instanceof Error && error.message) || 'Could not save the shopping list');
    }
  };

  const toggle = (index: number) => void write(items.map((item, i) => (i === index ? { ...item, checked: !item.checked } : item)));
  const add = () => {
    const text = adding.trim();
    if (!text) return;
    setAdding('');
    void write([...items, { text, checked: false }]);
  };
  const clearChecked = () => void write(items.filter((item) => !item.checked));

  const toSkylight = async () => {
    if (skylightOff) {
      toast.error('Skylight is not connected. Add the account in Settings first.');
      return;
    }
    const list = mirrorItems(writeShoppingList(items.filter((item) => !item.checked)));
    if (list.length === 0) {
      toast.error('Nothing left to buy');
      return;
    }
    setBusy('skylight');
    try {
      const out = await api.mirrorSkylightList({ items: list });
      if (out.status === 'SUCCESS') toast.success(out.message || `Added ${out.added ?? 0} item(s) to the Skylight list`);
      else toast.error(out.message || 'Skylight could not update the grocery list');
    } catch {
      toast.error('Skylight could not update the grocery list');
    } finally {
      setBusy(null);
    }
  };

  const left = items.filter((item) => !item.checked).length;
  const text = writeShoppingList(items);
  return (
    <div className="mx-auto max-w-2xl space-y-4" data-testid="shopping-list">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">Shopping list</h2>
          <p className="text-xs text-slate-400">{items.length ? `${left} of ${items.length} still to get` : 'Nothing on it yet'}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void toSkylight()} disabled={busy === 'skylight'} data-testid="list-send-skylight" className="glass-button min-h-11 px-3 text-xs disabled:opacity-60">
            {busy === 'skylight' ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Send to Skylight
          </button>
          <button type="button" onClick={() => onShare({ reference: 'Shopping list', text: shoppingShareText(text), heading: 'Share the shopping list to chat', buildMessage: (note) => shoppingShareText(text, note) })} className="glass-button min-h-11 px-3 text-xs">
            <Share2 size={14} /> Share
          </button>
        </div>
      </div>

      <div className="glass-panel rounded-3xl border border-white/5 p-4">
        {query.isLoading && <p className="flex items-center gap-2 text-sm text-slate-400"><Loader2 size={14} className="animate-spin" /> Reading the list…</p>}
        {query.isError && <p className="text-sm text-amber-200">Could not read the shopping list.</p>}
        {!query.isLoading && items.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-slate-400">
            <ShoppingCart size={28} className="text-slate-600" />
            Plan the week’s dinners, then build the list from them.
            <button type="button" onClick={onGoToWeek} className="glass-button min-h-11 px-4 text-xs">Go to the week</button>
          </div>
        )}
        <ul className="divide-y divide-white/5">
          {items.map((item, index) => (
            <li key={`${index}-${item.text}`}>
              <label className="flex min-h-12 cursor-pointer items-center gap-3 px-1 py-2">
                <input type="checkbox" checked={item.checked} onChange={() => toggle(index)} className="h-5 w-5 shrink-0 accent-emerald-400" />
                <span className={`text-sm ${item.checked ? 'text-slate-500 line-through' : 'text-slate-100'}`}>{item.text}</span>
              </label>
            </li>
          ))}
        </ul>
        <form className="mt-3 flex gap-2" onSubmit={(event) => { event.preventDefault(); add(); }}>
          <input value={adding} onChange={(event) => setAdding(event.target.value)} placeholder="Add something…" aria-label="Add to the shopping list" className="glass-input min-h-11 min-w-0 flex-1 px-3 text-base sm:text-sm" />
          <button type="submit" className="glass-button min-h-11 px-3 text-xs"><Plus size={14} /> Add</button>
        </form>
        {items.some((item) => item.checked) && (
          <button type="button" onClick={clearChecked} className="mt-3 flex min-h-11 items-center gap-1.5 text-xs text-slate-400 hover:text-white">
            <Trash2 size={13} /> Clear ticked items
          </button>
        )}
      </div>
    </div>
  );
}
