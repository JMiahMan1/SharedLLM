import CalendarApp from '../components/calendar/CalendarApp';
import { useDarkModeSync } from '../hooks/useDarkModeSync';

const Calendar = () => {
  const { isDark } = useDarkModeSync();
  return (
    <div style={{ background: isDark ? 'transparent' : '#f5efe3', minHeight: '100%' }}>
      <CalendarApp />
    </div>
  );
};

export default Calendar;
