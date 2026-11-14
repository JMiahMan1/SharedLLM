import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { ArrowDown, ArrowUp, Check, ChefHat, ChevronDown, Circle, ListChecks, Plus, Trash2 } from 'lucide-react';
import PageHeader from '../components/ui/PageHeader';
import { CardSkeletons, EmptyState, ErrorRetry } from '../components/ui/StateViews';
import { failureText } from '../lib/dashboards';
import {
  addItem, clearCompleted, dueText, getLists, isOverdue, moveItem, previousForMove, removeItem, splitItems, updateItem,
  type TodoItem, type TodoList,
} from '../lib/todoLists';

const KEY = ['lists'];

/**
 * The home's to-do lists (Home Assistant's To-do lists: the shopping list and
 * any Reminders-style lists), for every member of the household.
 *
 * The recipe shopping list (Recipes, built from the week's dinners and sent
 * to the Skylight frame) is a different thing: each person's own note, with
 * ingredients grouped by recipe. It is linked from here rather than merged in,
 * because folding a per-person note into the household lists would make two
 * places edit one list with different rules, and the Recipes page already
 * builds, ticks and mirrors it.
 */
export default function Lists() {
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { data: lists, isLoading, error, refetch } = useQuery({ queryKey: KEY, queryFn: getLists, refetchInterval: 30_000 });
  const chosen = params.get('list');
  const current = lists?.find((l) => l.entity_id === chosen) ?? lists?.[0] ?? null;

  const apply = (updated: TodoList) => {
    queryClient.setQueryData<TodoList[]>(KEY, (old) => (old ?? []).map((l) => (l.entity_id === updated.entity_id ? updated : l)));
  };
  const act = async (work: () => Promise<TodoList>, fallback: string): Promise<boolean> => {
    try {
      apply(await work());
      return true;
    } catch (err) {
      toast.error(failureText(err, fallback));
      void queryClient.invalidateQueries({ queryKey: KEY });
      return false;
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 pb-24" data-testid="lists-page">
      <PageHeader icon={ListChecks} tone="text-emerald-400" title="Lists"
        subtitle="The household's to-do and shopping lists. Ask Jarvis to add to them too.">
        {lists && lists.length > 1 && (
          <div role="tablist" aria-label="Lists" className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1">
            {lists.map((l) => (
              <button key={l.entity_id} role="tab" type="button" aria-selected={current?.entity_id === l.entity_id}
                onClick={() => setParams({ list: l.entity_id }, { replace: true })}
                className={`min-h-11 shrink-0 rounded-xl px-3 text-sm whitespace-nowrap ${current?.entity_id === l.entity_id
                  ? 'bg-emerald-500/20 text-emerald-100 font-semibold' : 'bg-white/5 text-slate-300'}`}>
                {l.name}{l.open ? <span className="ml-1.5 text-xs opacity-80">{l.open}</span> : null}
              </button>
            ))}
          </div>
        )}
      </PageHeader>

      {isLoading && <CardSkeletons count={1} className="grid" height="h-64" label="Loading lists" />}
      {error && <ErrorRetry title={failureText(error, 'Could not load the lists')} onRetry={() => void refetch()} />}
      {lists && lists.length === 0 && (
        <EmptyState icon={<ListChecks size={28} />} title="No lists yet"
          detail="An admin can add a to-do list or the shopping list under Devices, Add an integration." />
      )}
      {current && <ListCard key={current.entity_id} list={current} act={act} />}

      <Link to="/recipes?tab=list" data-testid="recipe-list-link"
        className="glass-card rounded-2xl p-4 flex items-center gap-3 min-h-11 hover:bg-white/5">
        <ChefHat size={20} className="text-orange-300 shrink-0" aria-hidden />
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-white">Recipe shopping list</span>
          <span className="block text-xs text-slate-400">Made from your week of dinners, in Recipes.</span>
        </span>
      </Link>
    </div>
  );
}

type Act = (work: () => Promise<TodoList>, fallback: string) => Promise<boolean>;

function ListCard({ list, act }: { list: TodoList; act: Act }) {
  const [text, setText] = useState('');
  const [due, setDue] = useState('');
  const [busy, setBusy] = useState(false);
  const { open, done } = splitItems(list.items);
  const id = list.entity_id;

  const add = async () => {
    const summary = text.trim();
    if (!summary) return;
    setBusy(true);
    const ok = await act(() => addItem(id, summary, due ? { due_date: due } : {}), 'Could not add that');
    setBusy(false);
    if (ok) {
      setText('');
      setDue('');
    }
  };

  if (!list.available) {
    return <ErrorRetry title={`${list.name} is not available right now`} />;
  }
  return (
    <section className="glass-card rounded-2xl p-4 space-y-3" aria-label={list.name} data-testid="todo-list">
      <h2 className="text-base font-bold text-white">{list.name}</h2>
      <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); void add(); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={255} placeholder="Add an item"
          aria-label={`Add to ${list.name}`} className="min-h-11 min-w-0 flex-1 basis-40 rounded-xl bg-white/5 border border-white/10 px-3 text-sm" />
        {list.features.due_date && (
          <input type="date" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due date (optional)"
            className="min-h-11 rounded-xl bg-white/5 border border-white/10 px-2 text-sm" />
        )}
        <button type="submit" disabled={busy || !text.trim()}
          className="min-h-11 rounded-xl bg-emerald-500/20 px-4 text-sm font-semibold text-emerald-100 disabled:opacity-50 flex items-center gap-1">
          <Plus size={16} aria-hidden /> Add
        </button>
      </form>

      {open.length === 0 && <p className="text-sm text-slate-400 py-2">Nothing to do on this list.</p>}
      <ul className="divide-y divide-white/5" aria-label="To do">
        {open.map((item, index) => (
          <ItemRow key={item.uid} list={list} item={item} act={act}
            canUp={list.features.move && index > 0} canDown={list.features.move && index < open.length - 1} />
        ))}
      </ul>

      {done.length > 0 && (
        <details className="group rounded-xl bg-white/[0.03] px-3" data-testid="completed-items">
          <summary className="min-h-11 flex cursor-pointer list-none items-center gap-2 text-sm text-slate-300">
            <ChevronDown size={16} className="transition-transform group-open:rotate-180" aria-hidden />
            Done ({done.length})
          </summary>
          <ul className="divide-y divide-white/5" aria-label="Done">
            {done.map((item) => <ItemRow key={item.uid} list={list} item={item} act={act} canUp={false} canDown={false} />)}
          </ul>
          <div className="flex justify-end py-2">
            <button type="button" onClick={() => void act(() => clearCompleted(id), 'Could not clear the done items')}
              className="min-h-11 rounded-xl bg-white/5 px-3 text-sm text-slate-200">Clear done items</button>
          </div>
        </details>
      )}
    </section>
  );
}

function ItemRow({ list, item, act, canUp, canDown }: {
  list: TodoList; item: TodoItem; act: Act; canUp: boolean; canDown: boolean;
}) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(item.summary);
  const id = list.entity_id;
  const done = item.status === 'completed';

  const move = (dir: -1 | 1) => {
    const previous = previousForMove(list.items, item.uid, dir);
    if (previous === undefined) return;
    void act(() => moveItem(id, item.uid, previous), 'Could not move that');
  };
  const rename = async () => {
    const summary = name.trim();
    setRenaming(false);
    if (!summary || summary === item.summary) {
      setName(item.summary);
      return;
    }
    await act(() => updateItem(id, item.uid, { summary }), 'Could not rename that');
  };

  return (
    <li className="flex items-center gap-1 py-1" data-testid="todo-item">
      <button type="button" aria-label={done ? `Mark ${item.summary} not done` : `Mark ${item.summary} done`}
        aria-pressed={done}
        onClick={() => void act(() => updateItem(id, item.uid, { status: done ? 'needs_action' : 'completed' }),
          'Could not change that')}
        className="min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-lg text-emerald-300 hover:bg-white/5">
        {done ? <Check size={18} /> : <Circle size={18} />}
      </button>
      <div className="min-w-0 flex-1">
        {renaming ? (
          <form onSubmit={(e) => { e.preventDefault(); void rename(); }}>
            <input value={name} onChange={(e) => setName(e.target.value)} onBlur={() => void rename()} maxLength={255}
              aria-label={`Rename ${item.summary}`} autoFocus
              className="w-full min-h-11 rounded-lg bg-white/5 border border-white/10 px-2 text-sm" />
          </form>
        ) : (
          <button type="button" onClick={() => { setName(item.summary); setRenaming(true); }}
            aria-label={`Rename ${item.summary}`}
            className={`w-full min-h-11 text-left text-sm break-words ${done ? 'text-slate-500 line-through' : 'text-white'}`}>
            {item.summary}
            {item.due && (
              <span className={`block text-xs ${!done && isOverdue(item.due) ? 'text-rose-300' : 'text-slate-400'}`}>
                Due {dueText(item.due)}
              </span>
            )}
            {item.description && <span className="block text-xs text-slate-400 truncate">{item.description}</span>}
          </button>
        )}
      </div>
      {canUp && (
        <button type="button" aria-label={`Move ${item.summary} up`} onClick={() => move(-1)}
          className="min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-lg text-slate-400 hover:bg-white/5">
          <ArrowUp size={16} />
        </button>
      )}
      {canDown && (
        <button type="button" aria-label={`Move ${item.summary} down`} onClick={() => move(1)}
          className="min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-lg text-slate-400 hover:bg-white/5">
          <ArrowDown size={16} />
        </button>
      )}
      <button type="button" aria-label={`Delete ${item.summary}`}
        onClick={() => void act(() => removeItem(id, item.uid), 'Could not delete that')}
        className="min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-lg text-slate-400 hover:text-rose-300 hover:bg-white/5">
        <Trash2 size={16} />
      </button>
    </li>
  );
}
