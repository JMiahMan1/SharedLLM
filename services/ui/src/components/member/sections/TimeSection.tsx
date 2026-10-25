import { useQuery } from '@tanstack/react-query';
import { Grid3x3 } from 'lucide-react';
import MemberCard from '../MemberCard';
import { HourHeatmap } from '../charts';
import ScreenTimeControls from '../../health/ScreenTimeControls';
import OfflineCodeSetting from '../../health/OfflineCodeSetting';
import RequestsCard from '../../guardian/RequestsCard';
import { activityQuery, hourHeatmap } from '../../../lib/memberPage';
import { formatDayLabel } from '../../../lib/healthHub';

/**
 * When in the day things happen, over the last 14 days. The phone reports
 * screen time as a daily total with no hours, so the hours come from the
 * activity feed (what was played, read, visited, texted), labelled as such.
 */
function HeatmapCard({ username }: { username: string }) {
  const { data, isLoading, isError } = useQuery(activityQuery(username));
  const map = hourHeatmap(data?.events || [], 14);
  const reaches = map.since && map.since > map.days[0] ? ` The record reaches back to ${formatDayLabel(map.since)}.` : '';
  return (
    <MemberCard
      title="When they are active, 14 days"
      icon={Grid3x3}
      testId="member-heatmap"
      wide
      isLoading={isLoading}
      error={isError ? 'Activity could not be loaded.' : null}
      empty={map.max === 0 ? 'No activity recorded in the last 14 days.' : null}
    >
      <HourHeatmap
        testId="member-heatmap-grid"
        days={map.days}
        cells={map.cells}
        max={map.max}
        dayLabel={(d) => new Date(`${d}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' })}
        title={`Things recorded per hour (music, videos, reading, sites, messages); darker is more, up to ${map.max} in an hour.${reaches}`}
      />
    </MemberCard>
  );
}

/** Time controls (Family Link's Controls tab): the weekly schedule and routines, the heatmap, requests answered, the offline code. */
export default function TimeSection({ username, name }: { username: string; name: string }) {
  return (
    <>
      <ScreenTimeControls username={username} name={name} />
      <HeatmapCard username={username} />
      <RequestsCard username={username} name={name} kinds={['time']} history />
      <OfflineCodeSetting />
    </>
  );
}
