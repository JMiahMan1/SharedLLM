import { useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { ArrowLeft, Camera, Clock, Flame, Link2, Loader2, Pencil, Save, Search, Utensils } from 'lucide-react';
import { api } from '../../services/api';
import { blobToBase64 } from '../../lib/base64';
import { errorText } from '../../lib/errorText';
import { appendAttachment, attachmentMarkdown } from '../../lib/noteAttachments';
import { PHOTO_LABEL, buildRecipeNote, nutritionLine, type ImportedRecipe, type RecipeSearchItem } from '../../lib/recipeImport';
import RecipePhoto from './RecipePhoto';

// The model sometimes flags a word with where it was ("simmer (line 8)"); that
// note is not part of the reading, so a fix replaces just the word itself.
const LINE_NOTE = /\s*[([]\s*line\s*\d+[^)\]]*[)\]]\s*$|\s*[-\u2013\u2014]\s*line\s*\d+\s*$/i;

/**
 * Four ways into the cookbook: search the 300,000-recipe Food.com index (with
 * photos and per-serving nutrition), paste a recipe page, photograph a card or
 * a handwritten page, or write one out. A found recipe is previewed first;
 * saving keeps its photo beside it.
 */
export default function AddRecipe({
  existingTitles,
  asUser,
  onSaved,
  onWriteOwn,
  onLog,
  onBack,
}: {
  existingTitles: string[];
  asUser?: 'admin';
  onSaved: (path: string | null) => void;
  onWriteOwn: () => void;
  onLog: (title: string, body: string) => void;
  onBack: () => void;
}) {
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<RecipeSearchItem[]>([]);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [url, setUrl] = useState('');
  const [importing, setImporting] = useState(false);
  const [imported, setImported] = useState<ImportedRecipe | null>(null);
  const [saving, setSaving] = useState(false);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [photoRaw, setPhotoRaw] = useState('');
  const [photoDraft, setPhotoDraft] = useState('');
  const [uncertain, setUncertain] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<Record<string, string[]>>({});
  const [fixes, setFixes] = useState<Record<string, string>>({});
  const [fixed, setFixed] = useState<string[]>([]);
  const [missing, setMissing] = useState<string[]>([]);
  const [readingPhoto, setReadingPhoto] = useState(false);
  const photoRef = useRef<HTMLInputElement>(null);

  const sourcesQuery = useQuery({ queryKey: ['recipe-sources'], queryFn: () => api.recipeSources(), staleTime: 30_000, retry: false });
  const sources = useMemo(() => new Set(sourcesQuery.data ?? []), [sourcesQuery.data]);
  const titles = useMemo(() => new Set(existingTitles.map((t) => t.toLowerCase())), [existingTitles]);

  const clearPhoto = () => {
    setPhotoFile(null);
    setPhotoRaw('');
    setPhotoDraft('');
    setUncertain([]);
    setSuggestions({});
    setFixes({});
    setFixed([]);
    setMissing([]);
  };

  const search = async () => {
    const q = query.trim();
    if (q.length < 2) {
      toast.error('Type at least two letters to search');
      return;
    }
    setSearching(true);
    try {
      const res = await api.searchRecipes(q);
      const found = (res.detail?.results ?? res.detail?.recipes ?? []) as RecipeSearchItem[];
      setResults(found);
      if (res.status !== 'SUCCESS' || found.length === 0) toast.error(res.message || 'No recipes found');
    } catch {
      toast.error('The search could not be completed');
    } finally {
      setSearching(false);
    }
  };

  const openResult = async (item: RecipeSearchItem) => {
    clearPhoto();
    if (item.source !== 'foodcom' || item.id === undefined || item.id === null) {
      setImported(item as ImportedRecipe);
      return;
    }
    setLoadingId(item.source_url);
    try {
      const res = await api.fetchRecipe(Number(item.id));
      if (res.status !== 'SUCCESS' || !res.detail?.recipe) {
        toast.error(res.message || 'Could not read that recipe');
        return;
      }
      setImported(res.detail.recipe as ImportedRecipe);
    } catch {
      toast.error('Could not read that recipe');
    } finally {
      setLoadingId(null);
    }
  };

  const readLink = async () => {
    const link = url.trim();
    if (!link) {
      toast.error('Paste a recipe link first');
      return;
    }
    setImporting(true);
    clearPhoto();
    try {
      const res = await api.importRecipe(link);
      if (res.status === 'SUCCESS' && res.detail?.recipe) setImported(res.detail.recipe as ImportedRecipe);
      else {
        setImported(null);
        toast.error(res.message || 'That link could not be read');
      }
    } catch {
      toast.error('That link could not be read');
    } finally {
      setImporting(false);
    }
  };

  const readPhoto = async (file: File) => {
    setReadingPhoto(true);
    setImported(null);
    clearPhoto();
    try {
      const draft = await api.readRecipePhoto(file);
      const fromPhoto: ImportedRecipe = { ...draft.recipe, source_url: '', site: 'photo import', source_kind: 'photo' };
      setPhotoFile(file);
      setPhotoRaw(draft.raw_text);
      setUncertain(draft.uncertain);
      setSuggestions(draft.suggestions ?? {});
      // Each flagged word starts on its best reading so accepting is one click.
      setFixes(Object.fromEntries(draft.uncertain.map((p) => [p, (draft.suggestions?.[p]?.[0] ?? '').trim()])));
      setMissing(draft.missing);
      setPhotoDraft(buildRecipeNote(fromPhoto, [PHOTO_LABEL]));
      setImported(fromPhoto);
      if (draft.missing.length > 0) toast.error(`The photo did not give the ${draft.missing.join(', ')} - fill them in before saving`);
    } catch (error) {
      toast.error(errorText(error, 'That photo could not be read. Type the recipe instead.'));
    } finally {
      setReadingPhoto(false);
    }
  };

  // Apply one accepted reading to the draft. Nothing changes until this runs.
  const applyFix = (phrase: string) => {
    const value = (fixes[phrase] ?? '').trim();
    if (!value) {
      toast.error('Type the correction first');
      return;
    }
    const target = phrase.replace(LINE_NOTE, '').trim();
    if (target && photoDraft.includes(target)) {
      setPhotoDraft(photoDraft.replace(target, value));
      setFixed((prev) => (prev.includes(phrase) ? prev : [...prev, phrase]));
      return;
    }
    toast.error('Could not find that line in the draft - edit it directly');
  };

  const save = async () => {
    if (!imported) return;
    if (titles.has(imported.title.trim().toLowerCase())) {
      toast.error('A recipe with that name is already in the cookbook');
      return;
    }
    if (imported.source_url && sources.has(imported.source_url)) {
      toast.error('That recipe is already in the cookbook');
      return;
    }
    setSaving(true);
    try {
      const isPhoto = imported.source_kind === 'photo';
      const content = isPhoto && photoDraft ? photoDraft : buildRecipeNote(imported, isPhoto ? [PHOTO_LABEL] : []);
      const saved = await api.cookbookSave({ title: imported.title, content, as_user: asUser, image_url: isPhoto ? null : imported.image });
      if (saved.status !== 'SUCCESS') {
        toast.error(saved.message || 'Could not save the recipe');
        return;
      }
      const detail = (saved.detail ?? {}) as { path?: string; etag?: string | null };
      // Keep the photographed page with the recipe, linked from its body.
      if (isPhoto && photoFile && detail.path) {
        try {
          const up = await api.cookbookAttach({ path: detail.path, filename: photoFile.name || 'recipe-photo.jpg', data: await blobToBase64(photoFile) });
          const link = up.detail?.link;
          if (up.status === 'SUCCESS' && typeof link === 'string') {
            const name = typeof up.detail?.name === 'string' ? up.detail.name : photoFile.name || 'recipe-photo.jpg';
            await api.cookbookSave({ title: imported.title, content: appendAttachment(content, attachmentMarkdown(link, name, photoFile.type)), path: detail.path, if_match: detail.etag ?? undefined });
          } else {
            toast.error('Saved, but the photo could not be kept with it');
          }
        } catch {
          toast.error('Saved, but the photo could not be kept with it');
        }
      }
      toast.success('Added to the family cookbook');
      onSaved(detail.path ?? null);
    } catch {
      toast.error('Could not save the recipe');
    } finally {
      setSaving(false);
    }
  };

  const card = 'glass-panel space-y-3 rounded-3xl border border-white/5 p-5';
  return (
    <div className="space-y-4" data-testid="recipe-import">
      <button type="button" onClick={onBack} className="flex min-h-11 items-center gap-1.5 text-sm text-slate-300 hover:text-white">
        <ArrowLeft size={16} /> All recipes
      </button>

      {imported ? (
        <div className={card} data-testid="recipe-import-preview">
          <div className="flex flex-col gap-4 sm:flex-row">
            <RecipePhoto url={imported.source_kind === 'photo' ? null : imported.image} className="aspect-[4/3] w-full shrink-0 rounded-2xl sm:w-56" />
            <div className="min-w-0 space-y-1">
              <h2 className="text-xl font-bold text-white">{imported.title}</h2>
              <p className="text-xs text-slate-400">
                {imported.site}
                {imported.total_minutes ? ` · ${imported.total_minutes} min` : ''}
                {imported.yield ? ` · ${imported.yield}` : ''}
                {` · ${imported.ingredients.length} ingredients · ${imported.instructions.length} steps`}
              </p>
              {imported.description ? <p className="text-xs text-slate-500">{imported.description}</p> : null}
              {nutritionLine(imported) ? (
                <p className="text-xs text-emerald-300/80" data-testid="imported-nutrition">
                  {nutritionLine(imported)?.replace(/^\*[^*]*\*\s*/, 'Per serving: ')}
                </p>
              ) : null}
              {imported.source_kind !== 'photo' && imported.ingredients.length > 0 && (
                <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-slate-300">
                  {imported.ingredients.slice(0, 8).map((item) => <li key={item}>{item}</li>)}
                  {imported.ingredients.length > 8 ? <li className="list-none text-slate-500">and {imported.ingredients.length - 8} more…</li> : null}
                </ul>
              )}
            </div>
          </div>
          {imported.source_kind === 'photo' && (
            <textarea value={photoDraft} onChange={(event) => setPhotoDraft(event.target.value)} aria-label="Recipe draft" rows={10} className="glass-input w-full resize-y px-3 py-2 text-sm" />
          )}
          {imported.source_kind === 'photo' && (missing.length > 0 || uncertain.length > 0) ? (
            <div className="space-y-1 rounded-lg border border-amber-400/30 bg-amber-400/10 p-2 text-[11px] text-amber-200" data-testid="recipe-photo-review">
              {missing.length > 0 ? <div>The photo did not give the {missing.join(', ')} - fill them in before saving.</div> : null}
              {uncertain.length > 0 ? (
                <div className="space-y-2" data-testid="recipe-photo-uncertain">
                  <div>Check against the page - each one starts on a suggested reading you can accept or type over:</div>
                  {uncertain.map((phrase) => {
                    const readings = suggestions[phrase] ?? [];
                    const done = fixed.includes(phrase);
                    return (
                      <div key={phrase} className="flex flex-wrap items-center gap-1.5">
                        <span className={`min-w-0 max-w-full truncate ${done ? 'line-through opacity-60' : ''}`} title={phrase}>
                          {done ? '✓' : '•'} {phrase}
                        </span>
                        <input
                          value={fixes[phrase] ?? ''}
                          onChange={(event) => setFixes((prev) => ({ ...prev, [phrase]: event.target.value }))}
                          placeholder="Type the reading"
                          aria-label={`Correction for ${phrase}`}
                          className="glass-input min-w-0 flex-1 px-2 py-1 text-[11px]"
                        />
                        {readings.slice(1).map((reading) => (
                          <button key={reading} type="button" onClick={() => setFixes((prev) => ({ ...prev, [phrase]: reading }))} className="glass-button min-w-0 max-w-full truncate px-2 py-1 text-[11px]" title={`Use "${reading}"`}>
                            {reading}
                          </button>
                        ))}
                        <button type="button" onClick={() => applyFix(phrase)} aria-label={`Accept correction for ${phrase}`} disabled={!(fixes[phrase] ?? '').trim()} className="glass-button shrink-0 px-2 py-1 text-[11px] disabled:opacity-50">
                          Accept
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : null}
          {imported.source_kind === 'photo' && photoRaw ? (
            <details className="rounded-lg border border-white/10 bg-black/20 p-2">
              <summary className="cursor-pointer text-[11px] text-slate-400">Raw transcription from the photo</summary>
              <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-[11px] text-slate-300">{photoRaw}</pre>
            </details>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void save()} disabled={saving} className="glass-button min-h-11 px-4 text-sm disabled:opacity-60">
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
              Save to the cookbook
            </button>
            {imported.source_kind !== 'photo' && (
              <button type="button" onClick={() => onLog(imported.title, buildRecipeNote(imported))} data-testid="imported-log-health" className="glass-button min-h-11 px-4 text-sm">
                <Utensils size={14} /> Log to Health
              </button>
            )}
            <button type="button" onClick={() => { setImported(null); clearPhoto(); }} className="glass-button min-h-11 px-4 text-sm">
              Back to results
            </button>
          </div>
        </div>
      ) : (
        <>
          <section className={card}>
            <h2 className="text-lg font-bold text-white">Find a recipe</h2>
            <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); void search(); }}>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search 300,000 recipes: chicken chili, banana bread…"
                aria-label="Search recipes"
                enterKeyHint="search"
                type="search"
                className="glass-input min-h-11 min-w-0 flex-1 px-3 text-base sm:text-sm"
              />
              <button type="submit" disabled={searching} className="glass-button min-h-11 px-4 text-sm disabled:opacity-60">
                {searching ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
                Search
              </button>
            </form>
            <p className="text-[11px] text-slate-500">From the Food.com collection, with calories and protein per serving where the cook gave them.</p>
            {results.length > 0 && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4" data-testid="meal-results">
                {results.map((result) => {
                  const already = sources.has(result.source_url);
                  return (
                    <button
                      key={result.source_url}
                      type="button"
                      onClick={() => void openResult(result)}
                      disabled={already || loadingId === result.source_url}
                      data-testid={already ? 'meal-result-imported' : undefined}
                      className="flex flex-col overflow-hidden rounded-2xl border border-white/5 bg-white/[0.03] text-left transition hover:border-purple-400/40 disabled:opacity-60"
                    >
                      <div className="relative aspect-[4/3] w-full overflow-hidden">
                        <RecipePhoto url={result.image} className="h-full w-full" />
                        {loadingId === result.source_url && (
                          <span className="absolute inset-0 flex items-center justify-center bg-black/50"><Loader2 size={20} className="animate-spin text-white" /></span>
                        )}
                      </div>
                      <span className="space-y-1 p-2.5">
                        <span className="line-clamp-2 block text-xs font-semibold text-slate-100">{result.title}</span>
                        <span className="flex flex-wrap gap-1 text-[10px] text-slate-400" data-testid="meal-result-nutrition">
                          {result.total_minutes ? <span className="flex items-center gap-0.5"><Clock size={9} /> {result.total_minutes} min</span> : null}
                          {typeof result.calories === 'number' ? <span className="flex items-center gap-0.5"><Flame size={9} /> {Math.round(result.calories)} kcal</span> : null}
                          {typeof result.protein_g === 'number' ? <span>{Math.round(result.protein_g)} g protein</span> : null}
                        </span>
                        {already ? <span className="block text-[10px] text-emerald-300">Already in the cookbook</span> : null}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          <div className="grid gap-4 md:grid-cols-3">
            <section className={card}>
              <h3 className="flex items-center gap-2 text-sm font-semibold text-white"><Link2 size={15} /> From a link</h3>
              <p className="text-xs text-slate-400">Paste a recipe page; Jarvis reads its recipe and photo.</p>
              <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://…" aria-label="Recipe link" className="glass-input min-h-11 w-full px-3 text-base sm:text-sm" />
              <button type="button" onClick={() => void readLink()} disabled={importing} className="glass-button min-h-11 w-full px-3 text-sm disabled:opacity-60">
                {importing ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
                Read the link
              </button>
            </section>
            <section className={card}>
              <h3 className="flex items-center gap-2 text-sm font-semibold text-white"><Camera size={15} /> From a photo</h3>
              <p className="text-xs text-slate-400">A recipe card, cookbook page or handwriting, read by the local vision model.</p>
              <button type="button" onClick={() => photoRef.current?.click()} disabled={readingPhoto} data-testid="recipe-photo-button" className="glass-button min-h-11 w-full px-3 text-sm disabled:opacity-60">
                {readingPhoto ? <Loader2 size={14} className="animate-spin" /> : <Camera size={14} />}
                {readingPhoto ? 'Reading the photo…' : 'Take or choose a photo'}
              </button>
              <input
                ref={photoRef}
                type="file"
                accept="image/*"
                capture="environment"
                aria-label="Recipe photo"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void readPhoto(file);
                  event.target.value = '';
                }}
              />
            </section>
            <section className={card}>
              <h3 className="flex items-center gap-2 text-sm font-semibold text-white"><Pencil size={15} /> Write it yourself</h3>
              <p className="text-xs text-slate-400">A family recipe: name, ingredients and steps.</p>
              <button type="button" onClick={onWriteOwn} data-testid="recipe-write-own" className="glass-button min-h-11 w-full px-3 text-sm">
                <Pencil size={14} /> Start writing
              </button>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
