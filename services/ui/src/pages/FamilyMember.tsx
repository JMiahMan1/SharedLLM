import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Navigate, useParams, useSearchParams } from 'react-router-dom';
import { isAxiosError } from 'axios';
import { api } from '../services/api';
import { useAuth } from '../context/AuthContext';
import MemberStrip from '../components/member/MemberStrip';
import ProfileHeader from '../components/member/ProfileHeader';
import OverviewSection from '../components/member/sections/OverviewSection';
import TimeSection from '../components/member/sections/TimeSection';
import AppsWebSection from '../components/member/sections/AppsWebSection';
import ActivitySection from '../components/member/sections/ActivitySection';
import CommsSection from '../components/member/sections/CommsSection';
import LocationSection from '../components/member/sections/LocationSection';
import HealthSection from '../components/member/sections/HealthSection';
import FaithSection from '../components/member/sections/FaithSection';
import DevicesSection from '../components/member/sections/DevicesSection';
import { MEMBER_SECTIONS, memberPath, statusQuery, type SectionId } from '../lib/memberPage';

const SECTION_VIEW: Record<SectionId, (p: { username: string; name: string }) => ReactElement> = {
  overview: OverviewSection,
  comms: CommsSection,
  time: TimeSection,
  apps: AppsWebSection,
  activity: ActivitySection,
  location: LocationSection,
  health: HealthSection,
  faith: FaithSection,
  devices: DevicesSection,
};

/**
 * The family member page (docs/FAMILY_MEMBER_PAGE.md), modelled on Aura: the
 * children at a glance, then one child's profile with the urgent actions on
 * top and every section one tap away. Identity's visibility rule decides
 * which children the viewer may open; the server withholds the technical
 * side from everyone but the father and admins, and the Devices section
 * shows only when the server says this viewer may see it.
 */
export default function FamilyMember() {
  const { user } = useAuth();
  const me = user?.username || '';
  const myRole = user?.family_role;
  const { username } = useParams();
  const [params, setParams] = useSearchParams();
  const { data: family = [], isLoading, error } = useQuery({
    queryKey: ['family-roster'],
    queryFn: () => api.getFamily(),
    retry: false,
    staleTime: 10 * 60 * 1000,
  });
  // The children, and for the father and the mother each other too: they share
  // their collected data (identity's visibility rule says so as well).
  const children = useMemo(() => {
    const isParent = (role?: string | null) => role === 'father' || role === 'mother';
    return family.filter((m) => m.username !== me && (m.is_minor || (isParent(myRole) && isParent(m.family_role))));
  }, [family, me, myRole]);
  const child = children.find((c) => c.username === username);
  const { data: status } = useQuery({ ...statusQuery(child?.username || ''), enabled: Boolean(child) });
  const sections = MEMBER_SECTIONS.filter((s) => (!s.technical || status?.technical) && (!s.comms || status?.comms));
  const asked = params.get('section') as SectionId | null;
  const section = sections.find((s) => s.id === asked)?.id || 'overview';
  const View = SECTION_VIEW[section];
  const pick = (id: SectionId) => setParams(id === 'overview' ? {} : { section: id }, { replace: true });

  // Switching section or child brings the parent back to the top of what changed, not the middle of a long list.
  const contentTop = useRef<HTMLDivElement>(null);
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return; }
    const el = contentTop.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ block: 'start' });
  }, [section, child?.username]);
  // The phone's tab bar scrolls sideways: keep the chosen tab in view (a deep link to Health opens on Health, visibly).
  const tabsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const bar = tabsRef.current;
    const tab = bar?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!bar || !tab) return;
    const left = tab.offsetLeft - (bar.clientWidth - tab.offsetWidth) / 2;
    bar.scrollTo({ left: Math.max(0, left), behavior: firstRender.current ? 'auto' : 'smooth' });
  }, [section, child?.username, sections.length]);
  const [edges, setEdges] = useState({ left: false, right: false });
  useEffect(() => {
    const bar = tabsRef.current;
    if (!bar) return;
    const measure = () => setEdges({ left: bar.scrollLeft > 4, right: bar.scrollLeft + bar.clientWidth < bar.scrollWidth - 4 });
    measure();
    bar.addEventListener('scroll', measure, { passive: true });
    window.addEventListener('resize', measure);
    return () => { bar.removeEventListener('scroll', measure); window.removeEventListener('resize', measure); };
  }, [child?.username, sections.length]);
  // The bar takes a background only once it is stuck under the app header; in place it sits on the page.
  const barRef = useRef<HTMLDivElement>(null);
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const bar = barRef.current;
    const scroller = bar?.closest('main');
    if (!bar || !scroller) return;
    const check = () => setStuck(scroller.scrollTop > 0
      && (contentTop.current?.getBoundingClientRect().top ?? 1) < bar.getBoundingClientRect().top + 1);
    check();
    scroller.addEventListener('scroll', check, { passive: true });
    return () => scroller.removeEventListener('scroll', check);
  }, [child?.username]);

  if (!isLoading && !error && !child && children.length > 0) return <Navigate to={memberPath(children[0].username)} replace />;

  const tab = (s: (typeof sections)[number], vertical: boolean) => (
    <button
      key={s.id}
      type="button"
      role="tab"
      aria-selected={s.id === section}
      aria-controls="member-section-panel"
      aria-label={vertical ? undefined : s.label}
      onClick={() => pick(s.id)}
      data-testid={`member-${vertical ? 'rail' : 'tab'}-${s.id}`}
      className={`min-h-11 shrink-0 rounded-xl px-3 text-sm font-medium transition-colors ${vertical ? 'w-full text-left' : 'snap-start'} ${
        s.id === section ? 'bg-sky-400/15 text-white ring-1 ring-sky-400/30' : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'
      }`}
    >
      {vertical ? s.label : s.short}
    </button>
  );

  return (
    <div className="mx-auto w-full min-w-0 max-w-6xl space-y-3 px-1 pb-28 sm:space-y-4 sm:px-0" data-testid="family-member-page">
      {isLoading ? (
        <div className="h-40 skeleton rounded-2xl" aria-hidden />
      ) : isAxiosError(error) && error.response?.status === 403 ? (
        <p className="glass-panel rounded-2xl p-5 text-sm text-slate-300" data-testid="member-not-a-parent">
          This page is for parents. It shows each child to their own parents only.
        </p>
      ) : error ? (
        <p className="glass-panel rounded-2xl p-5 text-sm text-amber-300">The family list could not be loaded.</p>
      ) : !child ? (
        <p className="glass-panel rounded-2xl p-5 text-sm text-slate-300" data-testid="member-no-children">
          No children in the family yet. Family roles are set in Admin → Users → Family.
        </p>
      ) : (
        <>
          <MemberStrip members={children} selected={child.username} />
          <ProfileHeader key={child.username} child={child} />
          <div ref={contentTop} className="scroll-mt-4" aria-hidden />
          {/* Sticks flush under the app header on a phone (the shell scrolls <main>, padded 1 rem there), so cards never
              show through above it. Desktop has the rail instead. */}
          <div
            ref={barRef}
            className={`sticky -top-4 z-20 -mx-5 px-4 py-2 transition-colors sm:-mx-4 lg:hidden ${
              stuck ? 'bg-slate-950/90 shadow-lg shadow-black/30 backdrop-blur' : 'bg-transparent'
            }`}
            data-stuck={stuck || undefined}
          >
            <nav aria-label="Sections" className="relative">
              <div
                ref={tabsRef}
                className="flex snap-x [scrollbar-width:none] [&::-webkit-scrollbar]:hidden gap-1 overflow-x-auto rounded-2xl border border-white/10 bg-slate-900/80 p-1"
                role="tablist"
                aria-label={`Sections for ${child.display_name || child.username}`}
                data-testid="member-tabs"
              >
                {sections.map((s) => tab(s, false))}
              </div>
              {/* Says there is more to the right (and left) when the tabs do not all fit. */}
              {edges.left && <span className="pointer-events-none absolute inset-y-px left-px w-8 rounded-l-2xl bg-gradient-to-r from-slate-900 to-transparent" aria-hidden />}
              {edges.right && <span className="pointer-events-none absolute inset-y-px right-px w-8 rounded-r-2xl bg-gradient-to-l from-slate-900 to-transparent" aria-hidden data-testid="member-tabs-more" />}
            </nav>
          </div>
          <div className="lg:grid lg:grid-cols-[12rem_minmax(0,1fr)] lg:gap-4">
            <nav className="hidden lg:block" aria-label="Sections">
              <div className="glass-panel sticky top-4 space-y-1 rounded-2xl p-2" role="tablist" aria-orientation="vertical" data-testid="member-rail">
                {sections.map((s) => tab(s, true))}
              </div>
            </nav>
            <div
              key={`${child.username}-${section}`}
              className={
                section === 'overview'
                  ? // Cards of very different heights: two columns that stack them without the gaps a grid row leaves.
                    'min-w-0 space-y-3 sm:space-y-4 lg:columns-2 lg:gap-4 lg:space-y-0 [&>*]:break-inside-avoid lg:[&>*]:mb-4'
                  : 'grid min-w-0 grid-cols-1 content-start items-start gap-3 sm:gap-4 lg:grid-cols-2'
              }
              role="tabpanel"
              id="member-section-panel"
              aria-label={sections.find((s) => s.id === section)?.label}
              data-testid={`member-panel-${section}`}
            >
              <View username={child.username} name={child.display_name || child.username} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
