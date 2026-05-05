import { 
  Cloud, 
  GitBranch, 
  Music, 
  Mic, 
  FolderLock,
  Eye,
  EyeOff,
  ChevronRight
} from 'lucide-react';

const IntegrationTile = ({ name, icon: Icon, color, connected }: any) => (
  <div className="glass-card p-6 flex flex-col gap-4">
    <div className="flex items-center justify-between">
      <div className={`p-3 rounded-xl bg-${color}-500/20 text-${color}-400 border border-${color}-500/20`}>
        <Icon size={24} />
      </div>
      <span className={`text-[10px] uppercase font-bold px-2 py-1 rounded-md ${connected ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'}`}>
        {connected ? 'Connected' : 'Disconnected'}
      </span>
    </div>
    <div>
      <h3 className="font-bold text-white">{name}</h3>
      <p className="text-xs text-slate-500 mt-1">AES-256 Encrypted</p>
    </div>
    <button className="glass-button w-full text-xs mt-2">
      {connected ? 'Manage' : 'Connect'} <ChevronRight size={14} />
    </button>
  </div>
);

const Identity = () => {
  return (
    <div className="space-y-8">
      <header>
        <h2 className="text-2xl font-bold text-white">Identity Hub</h2>
        <p className="text-slate-400">Manage your personal credentials and AI persona</p>
      </header>

      <section>
        <h3 className="text-lg font-bold text-white mb-6">Integration Gallery</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          <IntegrationTile name="Nextcloud" icon={Cloud} color="blue" connected={true} />
          <IntegrationTile name="GitHub" icon={GitBranch} color="slate" connected={true} />
          <IntegrationTile name="Audiobookshelf" icon={Music} color="orange" connected={false} />
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <section className="glass-panel p-8">
          <div className="flex items-center justify-between mb-6">
            <h3 className="font-bold text-white flex items-center gap-2">
              <Mic size={18} className="text-pink-400" />
              Voice Persona Gallery
            </h3>
          </div>
          <div className="grid grid-cols-3 gap-4">
            {[
              { id: 'v1', name: 'Standard', color: 'purple' },
              { id: 'v2', name: 'Soft', color: 'blue' },
              { id: 'v3', name: 'Energetic', color: 'pink' }
            ].map((v) => (
              <div key={v.id} className="p-4 glass-card text-center cursor-pointer border-purple-500/0 hover:border-purple-500/50">
                <div className={`w-12 h-12 rounded-full bg-${v.color}-500/20 mx-auto mb-3 flex items-center justify-center text-${v.color}-400`}>
                   <Mic size={20} />
                </div>
                <p className="text-xs font-medium text-white">{v.name}</p>
              </div>
            ))}
          </div>
          <div className="mt-6 p-4 bg-white/5 rounded-xl">
             <label className="text-[10px] text-slate-500 uppercase block mb-2">Active Voice ID mapping</label>
             <code className="text-xs text-purple-300">elevenlabs://standard_v1_jeremiah</code>
          </div>
        </section>

        <section className="glass-panel p-8">
          <h3 className="font-bold text-white mb-6 flex items-center gap-2">
            <FolderLock size={18} className="text-emerald-400" />
            Memory Toggles
          </h3>
          <div className="space-y-4">
            {[
              { folder: '/nextcloud/photos/family', shared: true },
              { folder: '/nextcloud/docs/private', shared: false },
              { folder: '/github/projects/shared', shared: true }
            ].map((item, idx) => (
              <div key={idx} className="flex items-center justify-between p-4 glass-card">
                <div className="flex items-center gap-3">
                  {item.shared ? <Eye size={16} className="text-emerald-400" /> : <EyeOff size={16} className="text-slate-500" />}
                  <span className="text-xs font-mono text-slate-300">{item.folder}</span>
                </div>
                <button className={`text-[10px] px-3 py-1 rounded-full border ${item.shared ? 'border-emerald-500/30 text-emerald-400 bg-emerald-500/5' : 'border-slate-500/30 text-slate-400'}`}>
                  {item.shared ? 'Shared' : 'Private'}
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
};

export default Identity;
