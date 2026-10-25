import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ChevronRight, ShieldHalf } from 'lucide-react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';

/** The way into the parents' page, for someone the roster shows has children. */
export default function GuardianLink() {
  const user = useAuth().user;
  const { data: family = [] } = useQuery({
    queryKey: ['family-roster'],
    queryFn: () => api.getFamily(),
    enabled: Boolean(user?.username) && !user?.is_minor,
    retry: false,
    staleTime: 10 * 60 * 1000,
  });
  const children = family.filter((m) => m.is_minor && m.username !== user?.username);
  if (user?.is_minor || children.length === 0) return null;
  return (
    <Link
      to="/family/member"
      className="glass-panel flex min-h-11 items-center gap-3 rounded-2xl border border-sky-500/20 p-4 text-left"
      data-testid="guardian-link"
    >
      <ShieldHalf size={18} className="shrink-0 text-sky-300" />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-white">Family guardian</span>
        <span className="block text-xs text-slate-400">
          {children.map((c) => c.display_name || c.username).join(', ')}: the week, prayer prompts and controls
        </span>
      </span>
      <ChevronRight size={16} className="shrink-0 text-slate-500" />
    </Link>
  );
}
