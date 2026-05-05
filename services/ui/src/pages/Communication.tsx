import { 
  Send, 
  StickyNote, 
  Volume2, 
  CloudSun,
  Calendar
} from 'lucide-react';

const Communication = () => {
  return (
    <div className="h-full flex flex-col gap-8">
      <header>
        <h2 className="text-2xl font-bold text-white">Family Communication</h2>
        <p className="text-slate-400">Nextcloud-powered chat and proactive automation</p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 flex-1 min-h-0">
        <div className="lg:col-span-2 glass-panel flex flex-col overflow-hidden">
          <div className="p-4 border-b border-white/5 flex items-center justify-between">
            <h3 className="font-bold text-white flex items-center gap-2">
              <CloudSun size={18} className="text-blue-400" />
              Morning Briefing Configuration
            </h3>
            <button className="text-xs text-purple-400 hover:text-purple-300">Run Now</button>
          </div>
          
          <div className="flex-1 p-6 grid grid-cols-1 md:grid-cols-2 gap-6 overflow-y-auto">
            <div className="glass-card p-6 bg-blue-600/10 border-blue-500/20">
              <h4 className="text-sm font-bold text-white mb-4 flex items-center gap-2">
                <Calendar size={16} />
                Calendar Source
              </h4>
              <p className="text-xs text-slate-400 mb-4">Pulls from Nextcloud "Personal" and "Family" calendars.</p>
              <div className="space-y-2">
                <div className="text-[10px] text-slate-300 bg-white/5 p-2 rounded border border-white/5">
                  9:00 AM - School Drop-off
                </div>
                <div className="text-[10px] text-slate-300 bg-white/5 p-2 rounded border border-white/5">
                  6:00 PM - Dinner @ Grandma's
                </div>
              </div>
            </div>

            <div className="glass-card p-6 bg-orange-600/10 border-orange-500/20">
              <h4 className="text-sm font-bold text-white mb-4 flex items-center gap-2">
                <StickyNote size={16} />
                Sticky Notes
              </h4>
              <div className="space-y-3">
                <div className="p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
                  <p className="text-xs text-yellow-200">"Don't forget the umbrella!"</p>
                  <p className="text-[8px] text-yellow-500/50 mt-1">Set by Jeremiah</p>
                </div>
                <button className="w-full py-2 border border-dashed border-white/10 rounded-lg text-xs text-slate-500 hover:text-white transition-all">
                  + Add Note
                </button>
              </div>
            </div>

            <div className="md:col-span-2 glass-card p-6 bg-emerald-600/10 border-emerald-500/20">
              <h4 className="text-sm font-bold text-white mb-4 flex items-center gap-2">
                <Volume2 size={16} />
                Broadcast Settings
              </h4>
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked readOnly className="accent-emerald-500" />
                  <span className="text-xs text-slate-300">Kitchen Echo</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked readOnly className="accent-emerald-500" />
                  <span className="text-xs text-slate-300">Living Room TV</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" className="accent-emerald-500" />
                  <span className="text-xs text-slate-300">Office Speaker</span>
                </label>
              </div>
            </div>
          </div>
        </div>

        <div className="glass-panel flex flex-col bg-slate-900/40">
          <div className="p-4 border-b border-white/5">
            <h3 className="font-bold text-white flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-blue-500" />
              Family Messenger
            </h3>
          </div>
          
          <div className="flex-1 p-4 space-y-4 overflow-y-auto flex flex-col">
            <div className="self-start max-w-[80%] glass-card p-3 rounded-bl-none text-xs">
              <p className="text-blue-400 font-bold mb-1">AI Assistant</p>
              Hey kids! Don't forget it's Tuesday, which means soccer practice at 5 PM! ⚽
            </div>
            <div className="self-end max-w-[80%] bg-purple-600/30 border border-purple-500/30 p-3 rounded-xl rounded-br-none text-xs">
              <p className="text-purple-300 font-bold mb-1">Bob (Kid)</p>
              Can I have a snack before soccer?
            </div>
            <div className="self-start max-w-[80%] glass-card p-3 rounded-bl-none text-xs">
               <p className="text-blue-400 font-bold mb-1">AI Assistant</p>
               Sure! There are apples and yogurt in the fridge. 🍎
            </div>
          </div>

          <div className="p-4 bg-white/5">
            <div className="relative">
              <input 
                type="text" 
                placeholder="Message family..."
                className="w-full glass-input pr-12 text-sm h-10"
              />
              <button className="absolute right-2 top-1.5 p-1 text-purple-400 hover:text-white transition-colors">
                <Send size={18} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Communication;
