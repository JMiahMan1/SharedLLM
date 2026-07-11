import { Calendar as CalendarIcon } from 'lucide-react';
import CalendarApp from '../components/calendar/CalendarApp';

const Calendar = () => {
  return (
    <div className="space-y-6 md:space-y-8 pb-12">
      <header>
        <h2 className="text-2xl md:text-4xl font-black tracking-tighter text-white uppercase flex items-center gap-3">
          <CalendarIcon className="text-emerald-300" size={32} />
          Calendar
        </h2>
        <p className="mt-2 text-sm md:text-base text-slate-400">
          Your merged family agenda across every connected source.
        </p>
      </header>

      <CalendarApp />
    </div>
  );
};

export default Calendar;
