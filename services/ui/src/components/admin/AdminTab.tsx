/* eslint-disable react-refresh/only-export-components */
import { useSearchParams } from 'react-router-dom';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface AdminTabProps {
  isAdmin: boolean;
  children?: React.ReactNode;
}

export const AdminTab = ({ isAdmin, children }: AdminTabProps) => {
  if (!isAdmin) return null;
  return <>{children}</>;
};

export const useAdminTab = () => {
  const [searchParams] = useSearchParams();
  return searchParams.get('tab') === 'admin';
};

export const AdminBadge = () => (
  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-black uppercase tracking-wider bg-purple-500/20 text-purple-300 ml-auto">
    Admin
  </span>
);

export const AdminTabBar = ({
  isAdmin,
  tabs,
  activeTab,
  onTabChange,
}: {
  isAdmin: boolean;
  tabs: Array<{ id: string; label: string }>;
  activeTab: string;
  onTabChange: (tab: string) => void;
}) => {
  if (!isAdmin) return null;

  return (
    <div className="flex gap-2 mb-6 overflow-x-auto custom-scrollbar">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onTabChange(tab.id)}
          className={cn(
            "flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-medium transition-all whitespace-nowrap",
            activeTab === tab.id
              ? "bg-purple-600/30 text-white border border-purple-500/40"
              : "text-slate-400 hover:text-white hover:bg-white/5 border border-transparent"
          )}
        >
          <span>{tab.label}</span>
          {activeTab === tab.id && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300">
              Admin
            </span>
          )}
        </button>
      ))}
    </div>
  );
};

export default AdminTab;
