import { useMemo, type ReactElement } from 'react';
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
import LocationSection from '../components/member/sections/LocationSection';
import HealthSection from '../components/member/sections/HealthSection';
import FaithSection from '../components/member/sections/FaithSection';
import DevicesSection from '../components/member/sections/DevicesSection';
import { MEMBER_SECTIONS, memberPath, statusQuery, type SectionId } from '../lib/memberPage';

const SECTION_VIEW: Record<SectionId, (p: { username: string; name: string }) => ReactElement> = {
  overview: OverviewSection,
  time: TimeSection,
  apps: AppsWebSection,
  activity: ActivitySection,
  location: LocationSection,
  health: HealthSection,
  faith: FaithSection,
  devices: DevicesSection,
};

/** The old parents' page address (notices sent before the move use it). */
export function GuardianRedirect() {
  const [params] = useSearchParams();
  const child = params.get('child');
  return <Navigate to={child ? memberPath(child) : '/family/member'} replace />;
}

/**
 * The family member page (docs/FAMILY_MEMBER_PAGE.md), modelled on Aura: the
 * children at a glance, then one child's profile with the urgent actions on
 * top and every section one tap away. Identity's visibility rule decides
 * which children the viewer may open; the server withholds the technical
 * side from everyone but the father and admins, and the Devices section
 * shows only when the server says this viewer may see it.
 */
export default function FamilyMember() {
  const me = useAuth().user?.username || '';
  const { username } = useParams();
  const [params, setParams] = useSearchParams();
  const { data: family = [], isLoading, error } = useQuery({
    queryKey: ['family-roster'],
    queryFn: () => api.getFamily(),
    retry: false,
    staleTime: 10 * 60 * 1000,
  });
  const children = useMemo(() => family.filter((m) => m.is_minor && m.username !== me), [family, me]);
  const child = children.find((c) => c.username === username);
  const { data: status } = useQuery({ ...statusQuery(child?.username || ''), enabled: Boolean(child) });
  const sections = MEMBER_SECTIONS.filter((s) => !s.technical || status?.technical);
  const asked = params.get('section') as SectionId | null;
  const section = sections.find((s) => s.id === asked)?.id || 'overview';
  const View = SECTION_VIEW[section];
  const pick = (id: SectionId) => setParams(id === 'overview' ? {} : { section: id }, { replace: true });

  if (!isLoading && !error && !child && children.length > 0) return <Navigate to={memberPath(children[0].username)} replace />;

  const tab = (s: (typeof sections)[number], vertical: boolean) => (
    <button
      key={s.id}
      type="button"
      role="tab"
      aria-selected={s.id === section}
      onClick={() => pick(s.id)}
      data-testid={`member-${vertical ? 'rail' : 'tab'}-${s.id}`}
      className={`min-h-11 shrink-0 rounded-xl px-3 text-sm ${vertical ? 'w-full text-left' : ''} ${
        s.id === section ? 'bg-sky-400/15 text-white' : 'text-slate-400 hover:text-slate-200'
      }`}
    >
      {s.label}
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
          <nav
            className="sticky top-0 z-10 -mx-1 flex gap-1 overflow-x-auto bg-slate-950/80 px-1 py-1 backdrop-blur lg:hidden"
            role="tablist"
            aria-label="Sections"
            data-testid="member-tabs"
          >
            {sections.map((s) => tab(s, false))}
          </nav>
          <div className="lg:grid lg:grid-cols-[12rem_minmax(0,1fr)] lg:gap-4">
            <nav className="hidden lg:block" aria-label="Sections">
              <div className="glass-panel sticky top-4 space-y-1 rounded-2xl p-2" role="tablist" aria-orientation="vertical" data-testid="member-rail">
                {sections.map((s) => tab(s, true))}
              </div>
            </nav>
            <div
              key={`${child.username}-${section}`}
              className="grid min-w-0 grid-cols-1 content-start items-start gap-3 sm:gap-4 lg:grid-cols-2"
              role="tabpanel"
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
