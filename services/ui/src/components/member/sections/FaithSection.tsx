import RecapCard from '../../guardian/RecapCard';
import GuidanceCard from '../../guardian/GuidanceCard';
import JournalCard from '../../guardian/JournalCard';
import SuggestionLanesControls from '../../health/SuggestionLanesControls';

/** Faith and growth: the week in review, prayer and guidance, the talk journal, what Jarvis may suggest. */
export default function FaithSection({ username, name }: { username: string; name: string }) {
  return (
    <>
      <div className="min-w-0 lg:col-span-2">
        <RecapCard username={username} name={name} />
      </div>
      <GuidanceCard username={username} name={name} />
      <JournalCard username={username} name={name} />
      <SuggestionLanesControls username={username} name={name} />
    </>
  );
}
