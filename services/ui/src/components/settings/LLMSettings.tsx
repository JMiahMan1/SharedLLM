import React, { useState, useEffect } from 'react';
import { 
  Cpu, 
  Cloud, 
  Settings2, 
  Save, 
  Zap, 
  Brain, 
  Code, 
  Library,
  ShieldCheck,
  Globe,
  Key
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../services/api';
import toast from 'react-hot-toast';

const LLMSettings: React.FC = () => {
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [isSaving, setIsSaving] = useState(false);

  const { data: settings = [], isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.getSettings(),
  });

  const { data: availableModels = [] } = useQuery({
    queryKey: ['available-models'],
    queryFn: () => api.getAvailableModels(),
  });

  const saveMutation = useMutation({
    mutationFn: (payload: Record<string, string>) => api.updateSettingsBulk(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setDrafts({});
      toast.success('AI Compute Engine updated');
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to sync settings');
    },
    onSettled: () => setIsSaving(false)
  });

  const getSetting = (key: string) => drafts[key] ?? settings.find(s => s.key === key)?.value ?? '';

  const handleSave = () => {
    if (Object.keys(drafts).length === 0) return;
    setIsSaving(true);
    saveMutation.mutate(drafts);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center p-12 text-slate-500 animate-pulse">
        <Zap className="mr-2 animate-bounce" size={20} />
        Initializing Neural Pathways...
      </div>
    );
  }

  const activeProvider = getSetting('active_llm_provider') || 'ollama';

  return (
    <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-2xl font-black text-white flex items-center gap-3 tracking-tighter uppercase">
            <Brain className="text-purple-400" size={28} />
            AI & Compute Pane
          </h3>
          <p className="text-sm text-slate-400 mt-1">Single source of truth for LLM orchestration and model mapping.</p>
        </div>
        <button
          onClick={handleSave}
          disabled={isSaving || Object.keys(drafts).length === 0}
          className={`glass-button flex items-center gap-2 px-6 py-3 text-[10px] font-black uppercase tracking-widest transition-all ${
            Object.keys(drafts).length > 0 ? 'bg-purple-600/40 border-purple-500/50 text-white shadow-lg shadow-purple-500/20' : 'opacity-50 cursor-not-allowed'
          }`}
        >
          <Save size={16} />
          {isSaving ? 'Syncing...' : 'Commit Changes'}
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Provider Selection */}
        <div className="lg:col-span-1 space-y-6">
          <div className="glass-panel p-6 border-indigo-500/20 bg-indigo-500/5">
            <div className="flex items-center gap-3 mb-6">
              <Zap size={20} className="text-indigo-400" />
              <h4 className="text-xs font-black uppercase tracking-widest text-white">Active Provider</h4>
            </div>
            
            <div className="space-y-3">
              {[
                { id: 'ollama', label: 'Local (Ollama)', icon: Cpu, desc: 'Privacy-first, low latency local inference.' },
                { id: 'openrouter', label: 'Cloud (OpenRouter)', icon: Cloud, desc: 'High-power inference via cloud providers.' }
              ].map(provider => (
                <button
                  key={provider.id}
                  onClick={() => setDrafts({ ...drafts, active_llm_provider: provider.id })}
                  className={`w-full text-left p-4 rounded-2xl border transition-all duration-300 ${
                    activeProvider === provider.id 
                      ? 'bg-indigo-600/20 border-indigo-500/50 shadow-lg shadow-indigo-500/10 scale-[1.02]' 
                      : 'bg-black/20 border-white/5 hover:border-white/10'
                  }`}
                >
                  <div className="flex items-center gap-3 mb-1">
                    <provider.icon size={18} className={activeProvider === provider.id ? 'text-indigo-300' : 'text-slate-500'} />
                    <span className={`font-bold ${activeProvider === provider.id ? 'text-white' : 'text-slate-400'}`}>{provider.label}</span>
                  </div>
                  <p className="text-[10px] text-slate-500 leading-relaxed">{provider.desc}</p>
                </button>
              ))}
            </div>
          </div>

          <div className="glass-panel p-6 border-white/5">
            <div className="flex items-center gap-3 mb-6">
              <Globe size={20} className="text-emerald-400" />
              <h4 className="text-xs font-black uppercase tracking-widest text-white">Endpoints</h4>
            </div>
            <div className="space-y-4">
              <div>
                <label className="text-[9px] font-black uppercase tracking-widest text-slate-500 block mb-2">Local Ollama URL</label>
                <input 
                  type="text" 
                  value={getSetting('llm_local_url')} 
                  onChange={e => setDrafts({...drafts, llm_local_url: e.target.value})}
                  className="glass-input w-full text-xs"
                  placeholder="http://localhost:11434"
                />
              </div>
              <div>
                <label className="text-[9px] font-black uppercase tracking-widest text-slate-500 block mb-2">Cloud API URL</label>
                <input 
                  type="text" 
                  value={getSetting('llm_cloud_url')} 
                  onChange={e => setDrafts({...drafts, llm_cloud_url: e.target.value})}
                  className="glass-input w-full text-xs"
                  placeholder="https://openrouter.ai/api/v1"
                />
              </div>
              <div>
                <label className="text-[9px] font-black uppercase tracking-widest text-slate-500 block mb-2">Cloud API Key</label>
                <div className="relative">
                  <Key className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                  <input 
                    type="password" 
                    value={getSetting('llm_cloud_api_key')} 
                    onChange={e => setDrafts({...drafts, llm_cloud_api_key: e.target.value})}
                    onFocus={(e) => {
                      if (e.target.value.includes('***')) {
                        setDrafts({...drafts, llm_cloud_api_key: ''});
                      }
                    }}
                    className="glass-input w-full text-xs pl-10"
                    placeholder="sk-••••••••••••••••"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Model Mappings */}
        <div className="lg:col-span-2 space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Local Models */}
            <div className={`glass-panel p-6 border-white/5 transition-opacity ${activeProvider !== 'ollama' ? 'opacity-50' : 'opacity-100'}`}>
              <div className="flex items-center gap-3 mb-6">
                <Cpu size={20} className="text-orange-400" />
                <h4 className="text-xs font-black uppercase tracking-widest text-white">Local Model Mapping</h4>
              </div>
              <div className="space-y-6">
                {[
                  { label: 'Assistant', key: 'ollama_assistant_model', icon: Brain },
                  { label: 'Coding / Repair', key: 'ollama_coding_model', icon: Code },
                  { label: 'Librarian / RAG', key: 'ollama_librarian_model', icon: Library }
                ].map(role => (
                  <div key={role.key} className="glass-card p-4 bg-white/5">
                    <div className="flex items-center gap-2 mb-3">
                      <role.icon size={14} className="text-slate-500" />
                      <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">{role.label}</span>
                    </div>
                    <select
                      value={getSetting(role.key)}
                      onChange={e => setDrafts({...drafts, [role.key]: e.target.value})}
                      className="glass-input w-full text-xs bg-black/40"
                    >
                      <option value="">Select Local Model</option>
                      {availableModels.map(m => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
            </div>

            {/* Cloud Models */}
            <div className={`glass-panel p-6 border-white/5 transition-opacity ${activeProvider !== 'openrouter' ? 'opacity-50' : 'opacity-100'}`}>
              <div className="flex items-center gap-3 mb-6">
                <Cloud size={20} className="text-sky-400" />
                <h4 className="text-xs font-black uppercase tracking-widest text-white">Cloud Model Mapping</h4>
              </div>
              <div className="space-y-6">
                {[
                  { label: 'Assistant', key: 'cloud_assistant_model', icon: Brain, placeholder: 'google/gemini-2.0-flash-001' },
                  { label: 'Coding / Repair', key: 'cloud_coding_model', icon: Code, placeholder: 'anthropic/claude-3.5-sonnet' },
                  { label: 'Librarian / RAG', key: 'cloud_librarian_model', icon: Library, placeholder: 'google/gemini-2.0-flash-001' }
                ].map(role => (
                  <div key={role.key} className="glass-card p-4 bg-white/5">
                    <div className="flex items-center gap-2 mb-3">
                      <role.icon size={14} className="text-slate-500" />
                      <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">{role.label}</span>
                    </div>
                    <input 
                      type="text" 
                      value={getSetting(role.key)} 
                      onChange={e => setDrafts({...drafts, [role.key]: e.target.value})}
                      className="glass-input w-full text-xs"
                      placeholder={role.placeholder}
                    />
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="glass-panel p-6 border-emerald-500/20 bg-emerald-500/5">
            <div className="flex items-center gap-3 mb-4">
              <ShieldCheck size={20} className="text-emerald-400" />
              <h4 className="text-xs font-black uppercase tracking-widest text-white">Security Compliance</h4>
            </div>
            <p className="text-xs text-slate-400 leading-relaxed">
              API keys are stored using <strong>AES-256 Fernet encryption</strong>. Keys are never logged in raw format and are strictly masked in all UI responses. Internal service-to-service communication is verified via X-Internal-Secret handshakes.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LLMSettings;
