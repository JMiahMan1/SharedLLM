import { useState } from 'react';
import toast from 'react-hot-toast';
import { ArrowLeft, Loader2, Plus, Save, Send } from 'lucide-react';
import { api } from '../../services/api';
import { buildRecipeDoc, parseRecipeDoc, type RecipeDoc } from '../../lib/recipeDoc';

const TAG_CHOICES = ['breakfast', 'dinner', 'lunch', 'soup', 'salad', 'side', 'dessert', 'snack', 'bread', 'vegetarian', 'slow-cooker', 'instant-pot', 'grill', 'holiday', 'kid-favorite'];

function numberOrNull(value: string): number | null {
  const n = Number(value);
  return value.trim() && Number.isFinite(n) && n > 0 ? Math.round(n) : null;
}

/**
 * Write or change a recipe as fields: name, times, servings, tags, one
 * ingredient per line, one step per line, notes. The fields are written back
 * as the same markdown every other reader understands. Someone else's recipe
 * is not saved over: the change goes to its owner (or an admin) to approve.
 */
export default function RecipeEditor({
  path,
  initialTitle,
  initialBody,
  canChange,
  ownerLabel,
  etag,
  asUser,
  onSaved,
  onCancel,
}: {
  /** The recipe being changed; absent for a new one. */
  path?: string;
  initialTitle: string;
  initialBody: string;
  canChange: boolean;
  ownerLabel?: string;
  etag?: string;
  asUser?: 'admin';
  onSaved: (path: string | null) => void;
  onCancel: () => void;
}) {
  const start = parseRecipeDoc(initialBody);
  const [title, setTitle] = useState(initialTitle);
  const [doc, setDoc] = useState<RecipeDoc>(start);
  const [ingredients, setIngredients] = useState(start.ingredients.join('\n'));
  const [steps, setSteps] = useState(start.steps.join('\n'));
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const suggesting = Boolean(path) && !canChange;

  const set = <K extends keyof RecipeDoc>(key: K, value: RecipeDoc[K]) => setDoc((current) => ({ ...current, [key]: value }));
  const toggleTag = (tag: string) =>
    set('tags', doc.tags.includes(tag) ? doc.tags.filter((t) => t !== tag) : [...doc.tags, tag]);

  const save = async () => {
    const name = title.trim();
    if (!name) {
      toast.error('Give the recipe a name');
      return;
    }
    const content = buildRecipeDoc({
      ...doc,
      ingredients: ingredients.split('\n').map((line) => line.replace(/^\s*(?:[-*]\s*(?:\[[ xX]\]\s*)?)/, '').trim()).filter(Boolean),
      steps: steps.split('\n').map((line) => line.replace(/^\s*(?:\d+[.)]\s*|[-*]\s*)/, '').trim()).filter(Boolean),
    });
    setSaving(true);
    try {
      if (suggesting && path) {
        const sent = await api.cookbookSuggest({ path, content, message: message.trim() });
        if (sent.status === 'SUCCESS') {
          toast.success(sent.message || 'Change suggested');
          onSaved(path);
        } else {
          toast.error(sent.message || 'Could not send the suggestion');
        }
        return;
      }
      const saved = await api.cookbookSave({ title: name, content, path, if_match: path ? etag : undefined, as_user: path ? undefined : asUser });
      if (saved.status === 'SUCCESS') {
        toast.success('Recipe saved');
        if ((saved.detail as { left_old_copy?: boolean } | undefined)?.left_old_copy) {
          toast.error('Saved under the new name, but the old copy is still there');
        }
        onSaved(String((saved.detail as { path?: string } | undefined)?.path ?? path ?? '') || null);
      } else {
        toast.error(saved.message || 'Could not save the recipe');
      }
    } catch {
      toast.error(suggesting ? 'Could not send the suggestion' : 'Could not save the recipe');
    } finally {
      setSaving(false);
    }
  };

  const label = 'block text-[11px] font-semibold uppercase tracking-wider text-slate-400';
  return (
    <form
      className="glass-panel mx-auto max-w-3xl space-y-4 rounded-3xl border border-white/5 p-5"
      data-testid="recipe-editor"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <button type="button" onClick={onCancel} className="flex min-h-11 items-center gap-1.5 text-sm text-slate-300 hover:text-white">
        <ArrowLeft size={16} /> {path ? 'Back to the recipe' : 'All recipes'}
      </button>
      <h2 className="text-xl font-bold text-white">{suggesting ? 'Suggest a change' : path ? 'Edit recipe' : 'New recipe'}</h2>
      {suggesting && (
        <p className="rounded-xl border border-sky-400/30 bg-sky-400/10 px-3 py-2 text-xs text-sky-100">
          Only {ownerLabel || 'the person who added it'} can change this recipe. Your edits are sent to them to approve.
        </p>
      )}

      <label className={label}>
        Name
        <input id="recipe-name" value={title} onChange={(event) => setTitle(event.target.value)} aria-label="Recipe name" disabled={suggesting} className="glass-input mt-1 min-h-11 w-full px-3 text-base normal-case tracking-normal text-white sm:text-sm" />
      </label>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {([
          ['prepMinutes', 'Prep (min)'],
          ['cookMinutes', 'Cook (min)'],
          ['totalMinutes', 'Total (min)'],
          ['servings', 'Servings'],
        ] as const).map(([key, text]) => (
          <label key={key} className={label}>
            {text}
            <input
              inputMode="numeric"
              value={doc[key] ?? ''}
              onChange={(event) => set(key, numberOrNull(event.target.value))}
              aria-label={text}
              className="glass-input mt-1 min-h-11 w-full px-3 text-base normal-case tracking-normal text-white sm:text-sm"
            />
          </label>
        ))}
      </div>

      <fieldset>
        <legend className={label}>Tags</legend>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {[...new Set([...TAG_CHOICES, ...doc.tags])].map((tag) => (
            <button
              key={tag}
              type="button"
              aria-pressed={doc.tags.includes(tag)}
              onClick={() => toggleTag(tag)}
              className={`min-h-9 rounded-full border px-3 text-xs pointer-coarse:min-h-11 ${doc.tags.includes(tag) ? 'border-purple-400/60 bg-purple-500/25 text-white' : 'border-white/10 bg-white/5 text-slate-400'}`}
            >
              #{tag}
            </button>
          ))}
        </div>
      </fieldset>

      <label className={label}>
        Ingredients <span className="font-normal normal-case tracking-normal text-slate-500">(one per line, amount first: “1 ½ cups flour”)</span>
        <textarea value={ingredients} onChange={(event) => setIngredients(event.target.value)} aria-label="Ingredients" rows={8} className="glass-input mt-1 w-full resize-y px-3 py-2 text-base normal-case tracking-normal text-white sm:text-sm" />
      </label>
      <label className={label}>
        Steps <span className="font-normal normal-case tracking-normal text-slate-500">(one per line)</span>
        <textarea value={steps} onChange={(event) => setSteps(event.target.value)} aria-label="Steps" rows={8} className="glass-input mt-1 w-full resize-y px-3 py-2 text-base normal-case tracking-normal text-white sm:text-sm" />
      </label>
      <label className={label}>
        Notes
        <textarea value={doc.notes} onChange={(event) => set('notes', event.target.value)} aria-label="Notes" rows={3} className="glass-input mt-1 w-full resize-y px-3 py-2 text-base normal-case tracking-normal text-white sm:text-sm" />
      </label>
      <label className={label}>
        Source
        <input value={doc.source} onChange={(event) => set('source', event.target.value)} aria-label="Source" placeholder="Grandma, a cookbook, a link…" className="glass-input mt-1 min-h-11 w-full px-3 text-base normal-case tracking-normal text-white sm:text-sm" />
      </label>
      {suggesting && (
        <label className={label}>
          Why the change?
          <input value={message} onChange={(event) => setMessage(event.target.value)} aria-label="Note for the recipe's owner" maxLength={500} className="glass-input mt-1 min-h-11 w-full px-3 text-base normal-case tracking-normal text-white sm:text-sm" />
        </label>
      )}

      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={saving} className="glass-button min-h-11 px-4 text-sm disabled:opacity-60">
          {saving ? <Loader2 size={14} className="animate-spin" /> : suggesting ? <Send size={14} /> : path ? <Save size={14} /> : <Plus size={14} />}
          {suggesting ? 'Suggest this change' : path ? 'Save changes' : 'Save recipe'}
        </button>
        <button type="button" onClick={onCancel} className="glass-button min-h-11 px-4 text-sm">
          Cancel
        </button>
      </div>
    </form>
  );
}
