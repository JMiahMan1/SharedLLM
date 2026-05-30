import { useMemo } from 'react';
import { Activity, Zap, TrendingUp } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import type { UserWidgetSettings } from '../../types/widget';

interface EnergyInsightsWidgetProps {
  userSettings: UserWidgetSettings;
  onTogglePin: () => void;
  isPinned?: boolean;
}

interface EnergyDataPoint {
  time: string;
  power: number;
  battery?: number;
}

const mockEnergyData: EnergyDataPoint[] = [
  { time: '00:00', power: 45, battery: 88 },
  { time: '04:00', power: 32, battery: 86 },
  { time: '08:00', power: 120, battery: 92 },
  { time: '12:00', power: 180, battery: 95 },
  { time: '16:00', power: 95, battery: 91 },
  { time: '20:00', power: 150, battery: 89 },
  { time: '23:59', power: 65, battery: 87 },
];

const EnergyInsightsWidget = ({ userSettings, onTogglePin }: EnergyInsightsWidgetProps) => {
  const data = useMemo(() => mockEnergyData, []);
  const currentPower = data[data.length - 1]?.power || 0;
  const avgPower = useMemo(() => Math.round(data.reduce((sum, d) => sum + d.power, 0) / data.length), [data]);
  const maxPower = useMemo(() => Math.max(...data.map(d => d.power)), [data]);
  const currentBattery = data[data.length - 1]?.battery;

  return (
    <div className="glass-card h-full p-5 relative">
      <button
        onClick={onTogglePin}
        className="absolute top-3 right-3 text-slate-500 hover:text-purple-400 transition-colors"
        title={userSettings.is_pinned ? 'Unpin widget' : 'Pin widget'}
      >
        <Activity size={16} className={userSettings.is_pinned ? 'text-purple-400' : ''} />
      </button>

      <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
        <Zap size={18} className="text-amber-400" />
        Energy Insights
      </h3>

      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="glass-card p-3">
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Current</p>
          <p className="text-xl font-bold text-amber-400">{currentPower}<span className="text-xs ml-1 text-slate-500">W</span></p>
        </div>
        <div className="glass-card p-3">
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Average</p>
          <p className="text-xl font-bold text-blue-400">{avgPower}<span className="text-xs ml-1 text-slate-500">W</span></p>
        </div>
        <div className="glass-card p-3">
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Peak</p>
          <p className="text-xl font-bold text-red-400">{maxPower}<span className="text-xs ml-1 text-slate-500">W</span></p>
        </div>
      </div>

      {currentBattery !== undefined && (
        <div className="glass-card p-3 mb-4">
          <div className="flex items-center justify-between mb-1">
            <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">System Battery</p>
            <TrendingUp size={14} className="text-emerald-400" />
          </div>
          <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-emerald-500 to-emerald-400 rounded-full"
              style={{ width: `${currentBattery}%` }}
            />
          </div>
          <p className="text-sm font-bold text-emerald-400 mt-1">{currentBattery}%</p>
        </div>
      )}

      <div className="h-32">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data}>
            <defs>
              <linearGradient id="powerGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#64748b' }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fontSize: 10, fill: '#64748b' }} axisLine={false} tickLine={false} />
            <Tooltip
              contentStyle={{
                background: 'rgba(15, 23, 42, 0.9)',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: '8px',
                fontSize: '12px',
              }}
            />
            <Area type="monotone" dataKey="power" stroke="#f59e0b" strokeWidth={2} fill="url(#powerGradient)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default EnergyInsightsWidget;
