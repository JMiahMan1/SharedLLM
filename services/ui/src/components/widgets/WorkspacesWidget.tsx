import { useQuery } from '@tanstack/react-query';
import { FolderKanban } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import type { IActiveMediaWidgetProps } from '../../types/widget';
import type { Workspace } from '../../types/api';

/**
 * Dashboard widget that surfaces only workspaces relevant to the current user:
 * their own (`scope === 'user'` / `owner_user` match) and workspaces shared
 * with them (`scope === 'shared'`). Admins see the full registry. Designed as
 * the mobile-friendly replacement for the full Workspaces section on the
 * Dashboard (which is hidden for normal users on mobile).
 */
const WorkspacesWidget = ({ settingsButton }: IActiveMediaWidgetProps) => {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { data: workspaces = [], isLoading } = useQuery<Workspace[]>({
    queryKey: ['workspaces'],
    queryFn: () => api.getWorkspaces(),
    retry: 1,
  });

  const isAdmin = user?.is_admin;
  const relevant = isAdmin
    ? workspaces
    : workspaces.filter(
        (ws) => ws.owner_user === user?.username || ws.scope === 'shared' || ws.scope === 'user'
      );

  return (
    <div className="glass-panel h-full flex flex-col rounded-2xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <FolderKanban size={16} className="text-emerald-300" />
          <h3 className="text-sm font-bold text-white">Workspaces</h3>
        </div>
        {settingsButton}
      </div>
      <div className="flex-1 overflow-y-auto custom-scrollbar space-y-2">
        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton h-12 rounded-xl" />
            ))}
          </div>
        ) : relevant.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <FolderKanban size={28} className="text-slate-700 mb-2" />
            <p className="text-sm text-slate-500">No workspaces available</p>
          </div>
        ) : (
          relevant.map((ws) => (
            <button
              key={ws.id}
              onClick={() => navigate('/workspaces')}
              className="w-full flex items-start gap-3 p-3 rounded-xl bg-black/20 border border-white/5 hover:border-emerald-500/15 transition-all text-left"
            >
              <div className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${ws.available ? 'bg-emerald-400' : 'bg-red-400'}`} />
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-sm text-white truncate">{ws.display_name || ws.id}</p>
                <p className="mt-1 font-mono text-[9px] text-slate-600 break-all bg-black/20 px-2 py-1 rounded-md">
                  {ws.resolved_path || 'Path resolution failed'}
                </p>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
};

export default WorkspacesWidget;
