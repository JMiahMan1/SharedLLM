import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { AppWindow, ChevronDown, ChevronUp, ExternalLink, Settings2, Star } from 'lucide-react';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import {
  LEVEL_STYLES,
  LEVEL_WORDS,
  commonSenseChip,
  factChips,
  malwareWords,
  reviewsReadWords,
  lookupWords,
  sinceWords,
  sizeWords,
  unusedLine,
  worstLevel,
} from '../../lib/appVetting';
import MemberCard from '../member/MemberCard';
import type { AppAction, AppPolicy, AppRating, AppVerdict, GuardianApps, UnusedApp } from '../../types/api';

const SHOWN_AT_FIRST = 8;
const UNUSED_CHOICES = [14, 30, 60, 90, 180];
const BUTTON = 'min-h-9 pointer-coarse:min-h-11 rounded-xl border px-3 text-xs font-semibold transition-colors disabled:opacity-50';
const QUIET = `${BUTTON} border-white/10 bg-white/5 text-slate-200 hover:bg-white/10`;
const STRONG = `${BUTTON} border-sky-400/30 bg-sky-500/15 text-sky-100 hover:bg-sky-500/25`;
const WARN = `${BUTTON} border-amber-400/30 bg-amber-400/10 text-amber-100 hover:bg-amber-400/20`;

const appsQuery = (username: string) => ({
  queryKey: ['guardian-apps', username],
  queryFn: () => api.getGuardianApps(username),
  retry: false,
  staleTime: 60 * 1000,
  // While an app is still being checked the card refreshes itself.
  refetchInterval: (query: { state: { data?: GuardianApps } }) =>
    query.state.data?.apps.some((a) => a.lookup === 'checking' || a.lookup === 'pending' || a.review_state === 'checking')
      ? 30 * 1000
      : false,
});

function LevelBadge({ level }: { level: AppVerdict['level'] }) {
  if (!level) return null;
  return (
    <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${LEVEL_STYLES[level]}`}>
      {LEVEL_WORDS[level]}
    </span>
  );
}

function Chips({ words }: { words: string[] }) {
  if (!words.length) return null;
  return (
    <div className="flex min-w-0 flex-wrap gap-1">
      {words.map((w) => (
        <span key={w} className="max-w-full break-words rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] text-slate-300">
          {w}
        </span>
      ))}
    </div>
  );
}

type Run = (label: string, fn: () => Promise<unknown>) => void;

const SOURCE_WORDS: Record<string, string> = { relevant: 'Most relevant', newest: 'Newest', lowest: 'Low rated' };

/** The security scan: stalkerware, malware, and risky access, most serious first. */
function SecurityFindings({ app }: { app: AppVerdict }) {
  const malware = malwareWords(app.malware_check);
  if (!(app.security || []).length && !malware) return null;
  return (
    <section className="min-w-0 space-y-1.5" aria-label="Security" data-testid={`app-security-${app.package}`}>
      <h5 className="text-[11px] font-bold uppercase tracking-wider text-slate-400">Security</h5>
      {(app.security || []).length > 0 && (
        <ul className="space-y-1">
          {(app.security || []).map((f) => (
            <li key={`${f.check}-${f.key}`} className="flex min-w-0 items-start gap-1.5">
              <LevelBadge level={f.severity} />
              <span className="min-w-0 break-words text-slate-300">{f.text}</span>
            </li>
          ))}
        </ul>
      )}
      {malware && (
        <p className={`break-words ${app.malware_check?.state === 'malware' ? 'font-semibold text-red-200' : 'text-slate-400'}`}>
          {malware}
          {app.malware_check?.link && (
            <> · <a href={app.malware_check.link} target="_blank" rel="noreferrer" className="underline decoration-dotted">details</a></>
          )}
        </p>
      )}
    </section>
  );
}

/** Common Sense Media's review: its age, what parents need to know, and what parents and kids say. */
function CommonSenseReview({ app }: { app: AppVerdict }) {
  const cs = app.common_sense;
  if (!cs || cs.status !== 'found') return null;
  const over = app.common_sense_over_age;
  return (
    <section className="min-w-0 space-y-1.5 rounded-lg border border-white/5 bg-white/[0.02] p-2" aria-label="Common Sense Media" data-testid={`app-common-sense-${app.package}`}>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="font-semibold text-slate-100">Common Sense Media</span>
        {cs.age_label && (
          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${over ? 'border-amber-400/40 text-amber-100' : 'border-white/10 text-slate-300'}`}>
            {cs.age_label}{over && app.common_sense_limit != null ? ` (limit ${app.common_sense_limit})` : ''}
          </span>
        )}
        {cs.quality_stars != null && (
          <span className="inline-flex items-center gap-0.5 text-[11px] text-slate-400" aria-label={`${cs.quality_stars} of 5 for quality`}>
            <Star size={11} aria-hidden /> {cs.quality_stars}/5
          </span>
        )}
      </div>
      {cs.parents_need_to_know && <p className="break-words leading-relaxed text-slate-300">{cs.parents_need_to_know}</p>}
      {(cs.parents_say || cs.kids_say) && (
        <p className="text-slate-400">
          {cs.parents_say && `Parents say age ${cs.parents_say.age}+ (${cs.parents_say.reviews} reviews)`}
          {cs.parents_say && cs.kids_say && ' · '}
          {cs.kids_say && `Kids say age ${cs.kids_say.age}+ (${cs.kids_say.reviews} reviews)`}
        </p>
      )}
      {cs.url && (
        <a href={cs.url} target="_blank" rel="noreferrer" className="inline-flex min-h-9 items-center gap-1 text-sky-300 underline decoration-dotted pointer-coarse:min-h-11">
          Read the full review <ExternalLink size={12} aria-hidden />
        </a>
      )}
    </section>
  );
}

/** The Play reviews the household model's concerns rest on, word for word (never who wrote them). */
function ReviewExcerpts({ app }: { app: AppVerdict }) {
  const read = reviewsReadWords(app.reviews_read);
  // A quote a concern already shows is not repeated here.
  const quoted = new Set(app.concerns.flatMap((c) => c.evidence || []).map((q) => q.trim().toLowerCase()));
  const excerpts = (app.review_excerpts || []).filter((r) => !quoted.has(r.text.trim().toLowerCase()));
  if (!excerpts.length && !read) return null;
  return (
    <section className="min-w-0 space-y-1.5" aria-label="What reviewers said" data-testid={`app-reviews-${app.package}`}>
      {excerpts.length > 0 && (
        <>
          <h5 className="text-[11px] font-bold uppercase tracking-wider text-slate-400">More from the reviews</h5>
          <ul className="space-y-1.5">
            {excerpts.map((r) => (
              <li key={`${r.source}-${r.at}-${r.text.slice(0, 40)}`} className="min-w-0 border-l-2 border-white/10 pl-2">
                <p className="break-words italic text-slate-300">&ldquo;{r.text}&rdquo;</p>
                <p className="text-[11px] text-slate-500">
                  {r.stars} star{r.stars === 1 ? '' : 's'}{r.at ? ` · ${r.at}` : ''} · {SOURCE_WORDS[r.source] || r.source}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
      {read && <p className="text-[11px] text-slate-500">{read}</p>}
    </section>
  );
}

/** One app's detail: why, the review, the listing's facts, and what a parent can do. */
function AppDetail({ app, policy, name, run, busy }: { app: AppVerdict; policy: AppPolicy; name: string; run: Run; busy: boolean }) {
  const username = policy.username;
  const blocked = policy.blocked_apps.includes(app.package);
  const removing = policy.uninstall_apps.includes(app.package);
  const status = lookupWords(app);
  const safety = app.data_safety || {};
  return (
    <div className="min-w-0 space-y-2.5 border-t border-white/5 pt-2.5 text-xs" data-testid={`app-detail-${app.package}`}>
      {status && <p className="text-slate-400">{status}</p>}
      {app.summary && <p className="break-words leading-relaxed text-slate-200">{app.summary}</p>}
      {app.concerns.length > 0 && (
        <ul className="space-y-1.5">
          {app.concerns.map((c) => (
            <li key={c.category} className="min-w-0 rounded-lg border border-white/5 bg-white/[0.02] p-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <LevelBadge level={c.severity} />
                <span className="font-semibold text-slate-100">{c.label}</span>
              </div>
              {c.why && <p className="mt-1 break-words text-slate-300">{c.why}</p>}
              {(c.evidence || []).map((q) => (
                <p key={q} className="mt-1 break-words border-l-2 border-white/10 pl-2 italic text-slate-400">&ldquo;{q}&rdquo;</p>
              ))}
            </li>
          ))}
        </ul>
      )}
      <SecurityFindings app={app} />
      <CommonSenseReview app={app} />
      <ReviewExcerpts app={app} />
      <dl className="grid min-w-0 grid-cols-1 gap-x-3 gap-y-1 text-slate-400 sm:grid-cols-2">
        {app.developer && <div className="min-w-0 break-words"><dt className="inline">By </dt><dd className="inline text-slate-300">{app.developer}</dd></div>}
        {app.stars != null && <div><dt className="inline">Stars </dt><dd className="inline text-slate-300">{app.stars.toFixed(1)}{app.installs ? ` · ${app.installs} installs` : ''}</dd></div>}
        <div><dt className="inline">Last used </dt><dd className="inline text-slate-300">{app.last_used ? sinceWords(app.last_used) : 'not known'}</dd></div>
        {sizeWords(app.size_bytes) && <div><dt className="inline">Size </dt><dd className="inline text-slate-300">{sizeWords(app.size_bytes)}</dd></div>}
        {safety.shares_data != null && (
          <div className="min-w-0 break-words sm:col-span-2">
            <dt className="inline">Data </dt>
            <dd className="inline text-slate-300">
              {safety.shares_data ? `Shares ${safety.shared_types || 'data'} with others` : 'Shares nothing with others'}
              {safety.collects_data ? `; collects ${safety.collected_types || 'data'}` : ''}
            </dd>
          </div>
        )}
        {app.lookup === 'not_on_play' && app.permissions.length > 0 && (
          <div className="min-w-0 break-words sm:col-span-2"><dt className="inline">Can use </dt><dd className="inline text-slate-300">{app.permissions.join(', ')}</dd></div>
        )}
      </dl>
      <div className="flex flex-wrap gap-2" role="group" aria-label={`What to do about ${app.label}`}>
        {app.attention.length > 0 && !app.dismissed && (
          <button type="button" className={STRONG} disabled={busy}
            onClick={() => run(`${app.label} kept; it leaves the top until something new turns up`,
              () => api.dismissApp(username, { package: app.package, kind: 'flag', fingerprint: app.fingerprint }))}>
            Reviewed, keep
          </button>
        )}
        {app.dismissed && (
          <button type="button" className={QUIET} disabled={busy}
            onClick={() => run(`${app.label} is back at the top`, () => api.dismissApp(username, { package: app.package, kind: 'flag', undo: true }))}>
            Undo keep
          </button>
        )}
        <button type="button" className={blocked ? QUIET : WARN} disabled={busy}
          onClick={() => run(blocked ? `${app.label} opens again` : `${app.label} is blocked on ${name}'s devices`,
            () => api.appAction(username, app.package, (blocked ? 'unblock' : 'block') as AppAction))}>
          {blocked ? 'Unblock' : 'Block'}
        </button>
        {policy.can_uninstall && (
          <button type="button" className={removing ? QUIET : WARN} disabled={busy}
            onClick={() => run(removing ? `${app.label} stays installed` : `${app.label} will be removed`,
              () => api.appAction(username, app.package, removing ? 'cancel_remove' : 'remove'))}>
            {removing ? 'Cancel removal' : 'Remove'}
          </button>
        )}
        {app.override !== 'allow' && (
          <button type="button" className={QUIET} disabled={busy}
            onClick={() => run(`${app.label} will never be flagged`, () => api.putAppOverride(username, app.package, 'allow'))}>
            Always allow
          </button>
        )}
        {app.override !== 'flag' && (
          <button type="button" className={QUIET} disabled={busy}
            onClick={() => run(`${app.label} will always be flagged`, () => api.putAppOverride(username, app.package, 'flag'))}>
            Always flag
          </button>
        )}
        {app.override && (
          <button type="button" className={QUIET} disabled={busy}
            onClick={() => run(`The rules decide about ${app.label} again`, () => api.putAppOverride(username, app.package, null))}>
            Use the rules
          </button>
        )}
      </div>
      <p className="text-[11px] leading-relaxed text-slate-500">
        Block keeps it from opening on {name}&apos;s devices; it stays installed.{' '}
        {policy.can_uninstall
          ? 'Remove uninstalls it from devices where Jarvis is the device owner.'
          : 'Removing it from here needs Jarvis set up as the device owner; otherwise remove it on the device.'}
      </p>
    </div>
  );
}

function AppRow({ app, policy, name, run, busy, open, onToggle }: {
  app: AppVerdict; policy: AppPolicy; name: string; run: Run; busy: boolean; open: boolean; onToggle: () => void;
}) {
  const flagged = app.attention.length > 0 && !app.dismissed;
  const level = flagged ? worstLevel(app.attention.map((f) => f.level)) : null;
  const status = lookupWords(app);
  return (
    <li
      className={`min-w-0 rounded-xl border p-2.5 ${flagged ? 'border-amber-400/30 bg-amber-400/[0.04]' : 'border-white/5 bg-white/[0.02]'}`}
      data-testid={`app-row-${app.package}`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex min-h-9 w-full min-w-0 items-start gap-2 text-left pointer-coarse:min-h-11"
      >
        <span className="min-w-0 flex-1 space-y-1">
          <span className="flex min-w-0 flex-wrap items-center gap-1.5">
            <span className="min-w-0 break-words text-sm font-semibold text-white">{app.label}</span>
            {flagged && <LevelBadge level={level} />}
            {app.dismissed && <span className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] text-slate-400">Kept</span>}
            {app.override === 'allow' && <span className="rounded-full border border-emerald-400/30 px-2 py-0.5 text-[10px] text-emerald-200">Allowed</span>}
            {policy.blocked_apps.includes(app.package) && <span className="rounded-full border border-red-400/30 px-2 py-0.5 text-[10px] text-red-200">Blocked</span>}
          </span>
          {flagged && (
            <span className="block space-y-0.5">
              {app.attention.slice(0, 3).map((f) => (
                <span key={`${f.reason}-${f.category}`} className="block break-words text-xs text-amber-100/90">{f.text}</span>
              ))}
            </span>
          )}
          {!flagged && status && <span className="block text-[11px] text-slate-400">{status}</span>}
          <Chips words={[...factChips(app), commonSenseChip(app.common_sense)].filter((w): w is string => !!w)} />
        </span>
        {open ? <ChevronUp size={16} className="mt-0.5 shrink-0 text-slate-400" aria-hidden /> : <ChevronDown size={16} className="mt-0.5 shrink-0 text-slate-400" aria-hidden />}
      </button>
      {open && <AppDetail app={app} policy={policy} name={name} run={run} busy={busy} />}
    </li>
  );
}

/** The age rule: birth date, the rating limit (from the age or a parent's choice), and "not used" days. */
function RulesPanel({ policy, name, run, busy }: { policy: AppPolicy; name: string; run: Run; busy: boolean }) {
  const [birth, setBirth] = useState(policy.birth_date || '');
  const [rating, setRating] = useState<string>(policy.max_rating || '');
  const [days, setDays] = useState(policy.unused_days);
  const changed = birth !== (policy.birth_date || '') || rating !== (policy.max_rating || '') || days !== policy.unused_days;
  const field = 'min-h-11 w-full min-w-0 rounded-xl border border-white/10 bg-slate-900/60 px-3 text-sm text-white';
  return (
    <form
      className="min-w-0 space-y-2.5 rounded-xl border border-white/10 bg-white/[0.02] p-3"
      data-testid="apps-rules"
      onSubmit={(e) => {
        e.preventDefault();
        run('App rules saved', () => api.putAppPolicy(policy.username, {
          birth_date: birth || null,
          max_rating: (rating || null) as AppRating | null,
          unused_days: days,
        }));
      }}
    >
      <div className="grid min-w-0 grid-cols-1 gap-2.5 sm:grid-cols-3">
        <label className="min-w-0 space-y-1 text-[11px] font-medium text-slate-400">
          <span>{name}&apos;s birth date</span>
          <input type="date" className={field} value={birth} max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => setBirth(e.target.value)} data-testid="apps-birth-date" />
        </label>
        <label className="min-w-0 space-y-1 text-[11px] font-medium text-slate-400">
          <span>Highest rating</span>
          <select className={field} value={rating} onChange={(e) => setRating(e.target.value)} data-testid="apps-max-rating">
            <option value="">{policy.default_max_rating ? `By age (${policy.default_max_rating})` : 'By age (set a birth date)'}</option>
            {policy.ratings.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <label className="min-w-0 space-y-1 text-[11px] font-medium text-slate-400">
          <span>Suggest removing after</span>
          <select className={field} value={days} onChange={(e) => setDays(Number(e.target.value))} data-testid="apps-unused-days">
            {[...new Set([...UNUSED_CHOICES, policy.unused_days])].sort((a, b) => a - b).map((d) => (
              <option key={d} value={d}>{d} days unused</option>
            ))}
          </select>
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="submit" className={STRONG} disabled={busy || !changed}>Save rules</button>
        <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-slate-500">
          Only {name}&apos;s parents see or change this. A birth date sets the usual limit; choosing a rating overrides it.
        </p>
      </div>
    </form>
  );
}

function UnusedList({ unused, policy, name, run, busy }: { unused: UnusedApp[]; policy: AppPolicy; name: string; run: Run; busy: boolean }) {
  return (
    <section className="min-w-0 space-y-2" aria-label="Not used lately" data-testid="apps-unused">
      <h4 className="text-xs font-bold uppercase tracking-wider text-slate-300">Not used lately</h4>
      <p className="text-[11px] leading-relaxed text-slate-500">
        No use for {policy.unused_days} days or more.{' '}
        {policy.can_uninstall
          ? 'Remove uninstalls it; Block keeps it from opening; Keep hides the suggestion until it is used again.'
          : `Block keeps it from opening; Keep hides the suggestion until it is used again. To uninstall, remove it on ${name}'s device (Jarvis is not its device owner).`}
      </p>
      <ul className="space-y-2">
        {unused.map((app) => {
          const blocked = policy.blocked_apps.includes(app.package);
          const removing = policy.uninstall_apps.includes(app.package);
          return (
            <li key={app.package} className="min-w-0 rounded-xl border border-white/5 bg-white/[0.02] p-2.5" data-testid={`unused-${app.package}`}>
              <p className="break-words text-sm font-semibold text-white">{app.label}</p>
              <p className="text-[11px] text-slate-400">{unusedLine(app)}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {policy.can_uninstall && !removing && (
                  <button type="button" className={WARN} disabled={busy}
                    onClick={() => run(`${app.label} will be removed`, () => api.appAction(policy.username, app.package, 'remove'))}>
                    Remove
                  </button>
                )}
                {removing && <span className="self-center text-[11px] text-slate-400">Removal queued</span>}
                {!blocked && (
                  <button type="button" className={WARN} disabled={busy}
                    onClick={() => run(`${app.label} is blocked on ${name}'s devices`, () => api.appAction(policy.username, app.package, 'block'))}>
                    Block
                  </button>
                )}
                <button type="button" className={QUIET} disabled={busy}
                  onClick={() => run(`Keeping ${app.label}`, () => api.dismissApp(policy.username, { package: app.package, kind: 'unused' }))}>
                  Keep
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * App vetting for one child (docs/APP_VETTING.md): every app on their devices
 * with its Play rating and the household model's review, the questionable ones
 * first (over the age limit, a parent's flag, or a concern at or above this
 * parent's notify level), the age rule, and apps not used lately. Only the
 * child's parents and admins can load it; the server refuses everyone else.
 */
export default function AppsCard({ username, name }: { username: string; name: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery(appsQuery(username));
  const [open, setOpen] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [rules, setRules] = useState(false);
  const action = useMutation({
    mutationFn: ({ fn }: { label: string; fn: () => Promise<unknown> }) => fn(),
    onSuccess: (_d, { label }) => {
      toast.success(label);
      void queryClient.invalidateQueries({ queryKey: ['guardian-apps', username] });
    },
    onError: (err) => toast.error(errorText(err, 'That did not save. Try again.')),
  });
  const run: Run = (label, fn) => action.mutate({ label, fn });
  const busy = action.isPending;

  const flagged = (data?.apps || []).filter((a) => a.attention.length > 0 && !a.dismissed);
  const rest = (data?.apps || []).filter((a) => !(a.attention.length > 0 && !a.dismissed));
  const shown = showAll ? rest : rest.slice(0, SHOWN_AT_FIRST);
  const policy = data?.policy;
  const limitLine = policy
    ? policy.effective_max_rating
      ? `${policy.age != null ? `Age ${policy.age} · ` : ''}apps up to ${policy.effective_max_rating}${policy.max_rating ? ' (set by a parent)' : ''}`
      : 'No age set: ratings are shown but not compared'
    : '';

  return (
    <MemberCard
      title={`${name}'s apps`}
      icon={AppWindow}
      tone="text-violet-300"
      testId="apps-card"
      wide
      aside={data ? (data.attention ? `${data.attention} need a look` : `${data.apps.length} apps`) : undefined}
      isLoading={isLoading}
      error={error ? errorText(error, `${name}'s apps could not be loaded.`) : null}
    >
      {data && policy && (
        <div className="min-w-0 space-y-3">
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
            <p className="min-w-0 flex-1 break-words text-xs text-slate-300" data-testid="apps-limit">{limitLine}</p>
            <button type="button" className={QUIET} aria-expanded={rules} onClick={() => setRules(!rules)} data-testid="apps-rules-toggle">
              <span className="inline-flex items-center gap-1.5"><Settings2 size={14} aria-hidden /> Rules</span>
            </button>
          </div>
          {rules && <RulesPanel key={`${policy.birth_date}-${policy.max_rating}-${policy.unused_days}`} policy={policy} name={name} run={run} busy={busy} />}
          {data.lookups.paused_until && (
            <p className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-2.5 text-[11px] text-amber-200">
              Google Play lookups are paused until {new Date(data.lookups.paused_until).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })} after Google did not answer. Apps already checked are shown.
            </p>
          )}
          {(data.lookups.malware?.state === 'not_set_up' || data.lookups.malware?.state === 'key_rejected') && (
            <p className="rounded-xl border border-white/10 bg-white/[0.02] p-2.5 text-[11px] text-slate-400" data-testid="apps-malware-setup">
              {data.lookups.malware.state === 'key_rejected'
                ? 'abuse.ch refused the malware check key. An admin can replace it in Admin > Users & Devices > App checks.'
                : 'The malware check is not set up. An admin adds an abuse.ch key in Admin > Users & Devices > App checks.'}
            </p>
          )}
          {data.devices.length === 0 ? (
            <p className="rounded-xl border border-white/5 bg-white/[0.02] p-3 text-xs text-slate-400" data-testid="apps-card-none">
              No apps reported yet. {name}&apos;s Android device sends its list once it runs a Jarvis update with app checks.
            </p>
          ) : (
            <>
              {flagged.length > 0 && (
                <section className="min-w-0 space-y-2" aria-label="Needs a look" data-testid="apps-attention">
                  <h4 className="text-xs font-bold uppercase tracking-wider text-amber-200">Needs a look</h4>
                  <ul className="space-y-2">
                    {flagged.map((app) => (
                      <AppRow key={app.package} app={app} policy={policy} name={name} run={run} busy={busy}
                        open={open === app.package} onToggle={() => setOpen(open === app.package ? null : app.package)} />
                    ))}
                  </ul>
                </section>
              )}
              <section className="min-w-0 space-y-2" aria-label="All apps" data-testid="apps-all">
                <h4 className="text-xs font-bold uppercase tracking-wider text-slate-300">{flagged.length ? 'Everything else' : 'All apps'}</h4>
                <ul className="space-y-2">
                  {shown.map((app) => (
                    <AppRow key={app.package} app={app} policy={policy} name={name} run={run} busy={busy}
                      open={open === app.package} onToggle={() => setOpen(open === app.package ? null : app.package)} />
                  ))}
                </ul>
                {rest.length > SHOWN_AT_FIRST && (
                  <button type="button" className={QUIET} onClick={() => setShowAll(!showAll)} data-testid="apps-show-all">
                    {showAll ? 'Show fewer' : `Show all ${rest.length}`}
                  </button>
                )}
              </section>
              {data.unused.length > 0 && <UnusedList unused={data.unused} policy={policy} name={name} run={run} busy={busy} />}
            </>
          )}
          {data.stalkerware_list && (
            <p className="break-words text-[10px] leading-relaxed text-slate-500" data-testid="apps-attribution">
              Stalkerware matches use{' '}
              <a href={data.stalkerware_list.source} target="_blank" rel="noreferrer" className="underline decoration-dotted">
                {(data.stalkerware_list.attribution || 'Stalkerware Indicators').split(', http')[0]}
              </a>{' '}
              ({(data.stalkerware_list.license || '').split(' (')[0]}).
            </p>
          )}
        </div>
      )}
    </MemberCard>
  );
}
