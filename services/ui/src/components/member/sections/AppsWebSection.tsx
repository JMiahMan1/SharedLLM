import { useQuery } from '@tanstack/react-query';
import { Globe2 } from 'lucide-react';
import MemberCard from '../MemberCard';
import AppControls from '../../health/AppControls';
import WebFilterControls from '../../health/WebFilterControls';
import RequestsCard from '../../guardian/RequestsCard';
import { activityQuery, statusQuery } from '../../../lib/memberPage';
import { relativeTime } from '../../../lib/wanderTrips';

/** The sites the phone's web filter saw lately, visited and blocked. */
function RecentWebCard({ username }: { username: string }) {
  const { data, isLoading, isError } = useQuery(activityQuery(username));
  const web = (data?.events || []).filter((e) => e.feature === 'web').slice(0, 30);
  return (
    <MemberCard
      title="Recent websites"
      icon={Globe2}
      testId="member-recent-web"
      isLoading={isLoading}
      error={isError ? 'Websites could not be loaded.' : null}
      empty={web.length === 0 ? 'No websites reported lately. The web filter reports them once it is on.' : null}
    >
      <ul className="max-h-72 divide-y divide-white/5 overflow-y-auto">
        {web.map((e, i) => {
          const blocked = e.action === 'blocked';
          const visits = typeof e.detail?.visits === 'number' ? e.detail.visits : null;
          return (
            <li key={`${e.at}-${i}`} className="flex items-center gap-2 py-1.5 text-xs">
              <span className="min-w-0 flex-1 truncate text-slate-200">{e.item}</span>
              {blocked ? (
                <span className="shrink-0 rounded-full border border-amber-400/40 px-2 text-[10px] text-amber-200">blocked</span>
              ) : (
                visits !== null && <span className="shrink-0 text-[10px] text-slate-400">{visits} visit{visits === 1 ? '' : 's'}</span>
              )}
              <span className="shrink-0 text-[10px] text-slate-500">{relativeTime(e.at)}</span>
            </li>
          );
        })}
      </ul>
    </MemberCard>
  );
}

/** Apps and the web: each app's time and state, install approvals, the web filter and its lists. */
export default function AppsWebSection({ username, name }: { username: string; name: string }) {
  const { data: status, isLoading, isError } = useQuery(statusQuery(username));
  const rules = status?.can_manage ? status.rules : null;
  return (
    <>
      <RequestsCard username={username} name={name} kinds={['app', 'permission', 'site']} />
      {isLoading || isError || !rules ? (
        <MemberCard
          title="Apps and websites"
          icon={Globe2}
          testId="member-app-rules"
          isLoading={isLoading}
          error={isError ? 'The rules could not be loaded.' : null}
          empty={!isLoading && !isError ? 'Only their parents can change this.' : null}
        />
      ) : (
        <>
          <AppControls username={username} name={name} rules={rules} />
          <WebFilterControls username={username} name={name} rules={rules} />
        </>
      )}
      <RecentWebCard username={username} />
      <RequestsCard username={username} name={name} kinds={['app', 'permission', 'site']} history />
    </>
  );
}
