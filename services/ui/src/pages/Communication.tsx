import { useState } from 'react';
import { 
  Send, 
  Volume2, 
  CloudSun,
  Calendar,
  Clock,
  CheckCircle,
  Zap,
  MessageCircle,
  Mic,
  Settings
} from 'lucide-react';
import { api } from '../services/api';
import toast from 'react-hot-toast';

const Communication = () => {
  const [message, setMessage] = useState('');
  const [broadcastTarget, setBroadcastTarget] = useState<string[]>(['Kitchen Echo', 'Living Room TV']);
  const [timerDuration, setTimerDuration] = useState(10);
  const [timerLabel, setTimerLabel] = useState('Pasta Timer');

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!message.trim()) return;
    try {
      // TODO: Connect to Nextcloud Talk API via Gateway
      toast.success('Message routed to Nexus Talk');
      setMessage('');
    } catch {
      toast.error('Messaging failed');
    }
  };

  const handleStartTimer = async () => {
    try {
      await api.setTimer(timerDuration * 60, timerLabel);
      toast.success(`Timer started: ${timerLabel} (${timerDuration}m)`);
    } catch {
      toast.error('Failed to set timer');
    }
  };

  const handleBroadcast = async (briefingType: string) => {
    try {
      // TODO: Call Gateway /api/execute/broadcast
      toast.success(`Broadcasting ${briefingType} to selected devices`);
    } catch {
      toast.error('Broadcast failed');
    }
  };

  return (
    <div className="h-full flex flex-col gap-8 pb-12">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-6">
        <div>
          <h2 className="text-4xl font-black text-white tracking-tighter uppercase">Nexus Relay</h2>
          <p className="text-slate-400 mt-2">Family coordination hub & proactive orchestration</p>
        </div>
        <div className="flex gap-3">
           <button onClick={() => handleBroadcast('Morning Briefing')} className="glass-button px-6 bg-orange-600/30 border-orange-500/30 text-orange-400 text-[10px] font-black uppercase tracking-widest">
              <CloudSun size={16} /> Run Briefing
           </button>
        </div>
      </header>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-8 flex-1 min-h-0">
        <div className="xl:col-span-8 space-y-8 overflow-y-auto pr-2 custom-scrollbar">
          
          <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="glass-panel p-8 relative overflow-hidden group">
              <div className="absolute top-0 right-0 w-32 h-32 bg-blue-500/5 rounded-full blur-3xl" />
              <h3 className="font-bold text-white mb-6 flex items-center gap-2">
                <Calendar size={18} className="text-blue-400" />
                Nexus Schedule
              </h3>
              <div className="space-y-3">
                {[
                  { time: '09:00 AM', event: 'School Drop-off', tag: 'FAMILY' },
                  { time: '01:30 PM', event: 'Project Sync', tag: 'WORK' },
                  { time: '06:00 PM', event: 'Dinner @ Grandma\'s', tag: 'EVENT' }
                ].map((ev, i) => (
                  <div key={i} className="flex items-center gap-4 p-4 glass-card border-white/5 bg-white/5 group-hover:bg-white/10 transition-colors">
                    <div className="text-[10px] font-mono text-blue-400 font-bold bg-blue-500/10 px-2 py-1 rounded border border-blue-500/20">{ev.time}</div>
                    <div className="flex-1">
                      <p className="text-xs font-bold text-white">{ev.event}</p>
                      <p className="text-[9px] text-slate-500 font-black uppercase tracking-widest mt-1">{ev.tag}</p>
                    </div>
                    <CheckCircle size={14} className="text-slate-600" />
                  </div>
                ))}
              </div>
              <button className="w-full mt-4 py-3 border border-dashed border-white/10 rounded-xl text-[10px] font-black uppercase tracking-widest text-slate-500 hover:text-white transition-all">
                + Sync External Calendar
              </button>
            </div>

            <div className="glass-panel p-8 relative overflow-hidden group">
              <div className="absolute top-0 right-0 w-32 h-32 bg-orange-500/5 rounded-full blur-3xl" />
              <h3 className="font-bold text-white mb-6 flex items-center gap-2">
                <Clock size={18} className="text-orange-400" />
                Active Timers
              </h3>
              <div className="space-y-4">
                <div className="p-6 rounded-2xl bg-orange-500/10 border border-orange-500/20 flex flex-col items-center text-center">
                   <div className="text-4xl font-black text-orange-400 font-mono mb-2 tracking-tighter">04:20</div>
                   <p className="text-[10px] uppercase font-black tracking-widest text-orange-500/50">Pasta Timer</p>
                </div>
                <div className="grid gap-3">
                   <div className="flex gap-2">
                      <input 
                        type="text" 
                        value={timerLabel}
                        onChange={(e) => setTimerLabel(e.target.value)}
                        placeholder="Label..." 
                        className="glass-input flex-1 text-xs py-2" 
                      />
                      <input 
                        type="number" 
                        value={timerDuration}
                        onChange={(e) => setTimerDuration(parseInt(e.target.value))}
                        className="glass-input w-20 text-xs py-2 text-center" 
                      />
                   </div>
                   <button 
                     onClick={handleStartTimer}
                     className="glass-button w-full py-3 bg-orange-600/20 border-orange-500/20 text-[10px] font-black uppercase tracking-widest text-orange-400"
                   >
                     Initialize Execution Timer
                   </button>
                </div>
              </div>
            </div>
          </section>

          <section className="glass-panel p-8">
             <div className="flex items-center justify-between mb-8">
                <h3 className="font-bold text-white flex items-center gap-2">
                  <Volume2 size={18} className="text-emerald-400" />
                  Broadcast Matrix
                </h3>
                <span className="text-[10px] text-slate-500 uppercase font-black tracking-widest">Targeting HA Entities</span>
             </div>
             
             <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {['Kitchen Echo', 'Living Room TV', 'Office Speaker', 'Bedroom Dot'].map((target) => (
                  <button 
                    key={target}
                    onClick={() => {
                       if (broadcastTarget.includes(target)) {
                         setBroadcastTarget(broadcastTarget.filter(t => t !== target));
                       } else {
                         setBroadcastTarget([...broadcastTarget, target]);
                       }
                    }}
                    className={`p-4 rounded-2xl border transition-all flex flex-col items-center gap-3 text-center group ${
                      broadcastTarget.includes(target) 
                        ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-400 shadow-lg shadow-emerald-500/10' 
                        : 'bg-white/5 border-white/10 text-slate-500 hover:border-white/20'
                    }`}
                  >
                     <Volume2 size={24} className={broadcastTarget.includes(target) ? 'animate-pulse' : ''} />
                     <span className="text-[10px] font-bold uppercase tracking-widest">{target}</span>
                  </button>
                ))}
             </div>

             <div className="mt-8 p-6 glass-card bg-indigo-600/10 border-indigo-500/20">
                <div className="flex flex-col md:flex-row items-center gap-6">
                   <div className="p-4 rounded-full bg-indigo-500/20 text-indigo-400">
                      <Mic size={32} />
                   </div>
                   <div className="flex-1 text-center md:text-left">
                      <h4 className="font-bold text-white">Direct Broadcast</h4>
                      <p className="text-xs text-slate-400 mt-1">Speak directly through all selected home speakers via Home Assistant TTS.</p>
                   </div>
                   <button className="glass-button px-8 py-3 bg-indigo-600/40 border-indigo-500/30 text-[10px] font-black uppercase tracking-widest">
                      Push to Talk
                   </button>
                </div>
             </div>
          </section>
        </div>

        <div className="xl:col-span-4 glass-panel flex flex-col bg-slate-900/60 border-blue-500/10 overflow-hidden">
          <div className="p-6 border-b border-white/5 bg-white/5 flex items-center justify-between">
            <h3 className="font-bold text-white flex items-center gap-2">
              <MessageCircle size={18} className="text-blue-400" />
              Nexus Messenger
            </h3>
            <div className="flex items-center gap-2">
               <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
               <span className="text-[9px] uppercase font-black text-slate-500 tracking-widest">Live: Nextcloud Talk</span>
            </div>
          </div>
          
          <div className="flex-1 p-6 space-y-6 overflow-y-auto flex flex-col custom-scrollbar">
            <div className="self-start max-w-[85%] glass-card p-4 rounded-2xl rounded-bl-none text-xs border-blue-500/20 bg-blue-500/5">
              <div className="flex items-center gap-2 mb-2">
                 <div className="p-1 rounded-lg bg-blue-500/20 text-blue-400"><Zap size={12} /></div>
                 <p className="text-blue-400 font-black uppercase tracking-tighter">Jarvis Assistant</p>
              </div>
              <p className="text-slate-300 leading-relaxed italic">"Morning everyone! Just a reminder: Soccer practice at 5 PM. I've pre-conditioned the car and synchronized the team calendar."</p>
            </div>

            <div className="self-end max-w-[85%] bg-purple-600/30 border border-purple-500/30 p-4 rounded-2xl rounded-br-none text-xs">
              <div className="flex items-center justify-end gap-2 mb-2">
                 <p className="text-purple-300 font-black uppercase tracking-tighter">Jeremiah (Admin)</p>
                 <div className="w-5 h-5 rounded-lg bg-purple-500 flex items-center justify-center text-[10px] font-black text-white">J</div>
              </div>
              <p className="text-slate-100 leading-relaxed text-right">Thanks Jarvis. Can you check if the soccer kit is in the dryer?</p>
            </div>

            <div className="self-start max-w-[85%] glass-card p-4 rounded-2xl rounded-bl-none text-xs border-blue-500/20 bg-blue-500/5">
              <div className="flex items-center gap-2 mb-2">
                 <div className="p-1 rounded-lg bg-blue-500/20 text-blue-400"><Zap size={12} /></div>
                 <p className="text-blue-400 font-black uppercase tracking-tighter">Jarvis Assistant</p>
              </div>
              <p className="text-slate-300 leading-relaxed italic">"Scanning Home Assistant sensors... Yes, the dryer completed 15 minutes ago. It is currently at 45°C. Would you like me to announce 'Laundry Ready' in the living room?"</p>
            </div>
          </div>

          <div className="p-6 bg-black/40 border-t border-white/5">
            <form onSubmit={handleSendMessage} className="relative">
              <input 
                type="text" 
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Message family mesh..."
                className="w-full glass-input pr-12 text-xs h-12 bg-white/5 focus:bg-white/10"
              />
              <button 
                type="submit"
                className="absolute right-2 top-2 p-2 text-purple-400 hover:text-white hover:bg-purple-500/20 rounded-xl transition-all"
              >
                <Send size={20} />
              </button>
            </form>
            <div className="mt-3 flex items-center justify-between">
               <p className="text-[8px] text-slate-500 uppercase font-black tracking-widest">End-to-end encrypted</p>
               <button className="text-[8px] text-slate-500 hover:text-slate-300 flex items-center gap-1 uppercase font-black tracking-widest transition-colors">
                  <Settings size={10} /> Chat Settings
               </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Communication;
