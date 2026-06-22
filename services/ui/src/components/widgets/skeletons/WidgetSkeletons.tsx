import React from 'react';
import type { WidgetKey } from '../../../types/widget';

// Common header wrapper to keep skeletons aligned with WidgetCard headers
interface SkeletonHeaderProps {
  title: string;
  icon: string;
}

const SkeletonHeader: React.FC<SkeletonHeaderProps> = ({ title, icon }) => (
  <div className="flex items-center justify-between mb-4 shrink-0">
    <div className="flex items-center gap-2 min-w-0">
      <span className="text-slate-500 shrink-0 flex items-center justify-center">{icon}</span>
      <h4 className="text-sm font-bold text-slate-400 tracking-wide truncate">{title}</h4>
    </div>
    <div className="h-5 w-5 bg-white/5 rounded-full animate-pulse" />
  </div>
);

export const EnergyInsightsSkeleton: React.FC = () => (
  <div className="glass-panel overflow-hidden p-5 flex flex-col h-full w-full relative">
    <SkeletonHeader title="Energy Insights" icon="⚡" />
    <div className="flex-1 flex flex-col justify-between min-h-0">
      <div className="h-6 bg-white/5 rounded w-1/3 animate-pulse mb-3" />
      <div className="flex-1 flex items-end gap-2 px-2 py-4">
        <div className="w-full bg-white/5 rounded-t animate-pulse h-1/2" />
        <div className="w-full bg-white/5 rounded-t animate-pulse h-3/4" />
        <div className="w-full bg-white/5 rounded-t animate-pulse h-1/3" />
        <div className="w-full bg-white/5 rounded-t animate-pulse h-5/6" />
        <div className="w-full bg-white/5 rounded-t animate-pulse h-2/3" />
      </div>
      <div className="flex justify-between mt-2 pt-2 border-t border-white/5 text-[9px] text-slate-600">
        <div className="h-2 bg-white/5 rounded w-8 animate-pulse" />
        <div className="h-2 bg-white/5 rounded w-8 animate-pulse" />
      </div>
    </div>
  </div>
);

export const AmbientTimerSkeleton: React.FC = () => (
  <div className="glass-panel overflow-hidden p-5 flex flex-col h-full w-full relative">
    <SkeletonHeader title="Ambient Timer" icon="⏱️" />
    <div className="flex-1 flex flex-col items-center justify-center min-h-0 gap-4">
      {/* Circle placeholder */}
      <div className="relative w-24 h-24 rounded-full border-4 border-white/5 flex items-center justify-center animate-pulse">
        <div className="h-4 bg-white/5 rounded w-12" />
      </div>
      <div className="flex gap-2">
        <div className="h-6 w-12 bg-white/5 rounded animate-pulse" />
        <div className="h-6 w-12 bg-white/5 rounded animate-pulse" />
      </div>
    </div>
  </div>
);

export const QuickNotesSkeleton: React.FC = () => (
  <div className="glass-panel overflow-hidden p-5 flex flex-col h-full w-full relative">
    <SkeletonHeader title="Quick Notes" icon="📝" />
    <div className="flex-1 flex flex-col justify-between min-h-0">
      <div className="space-y-2 overflow-y-hidden">
        <div className="h-10 bg-white/5 rounded-lg w-full animate-pulse" />
        <div className="h-10 bg-white/5 rounded-lg w-full animate-pulse" />
        <div className="h-10 bg-white/5 rounded-lg w-full animate-pulse" />
      </div>
      <div className="h-8 bg-white/5 rounded-lg w-full animate-pulse mt-4" />
    </div>
  </div>
);

export const ActiveMediaSkeleton: React.FC = () => (
  <div className="glass-panel overflow-hidden p-5 flex flex-col h-full w-full relative">
    <SkeletonHeader title="Now Playing" icon="🎵" />
    <div className="flex-1 flex flex-col justify-between min-h-0">
      <div className="flex gap-3 items-center">
        <div className="w-16 h-16 bg-white/5 rounded-xl animate-pulse shrink-0" />
        <div className="flex-1 space-y-2 min-w-0">
          <div className="h-3.5 bg-white/5 rounded w-3/4 animate-pulse" />
          <div className="h-3.5 bg-white/5 rounded w-1/2 animate-pulse" />
        </div>
      </div>
      <div className="space-y-2 my-4">
        <div className="h-1 bg-white/5 rounded-full w-full animate-pulse" />
        <div className="flex justify-between">
          <div className="h-2 bg-white/5 rounded w-6 animate-pulse" />
          <div className="h-2 bg-white/5 rounded w-6 animate-pulse" />
        </div>
      </div>
      <div className="flex justify-center gap-4 items-center mb-1">
        <div className="h-8 w-8 bg-white/5 rounded-full animate-pulse" />
        <div className="h-10 w-10 bg-white/5 rounded-full animate-pulse" />
        <div className="h-8 w-8 bg-white/5 rounded-full animate-pulse" />
      </div>
    </div>
  </div>
);

export const ChoresProgressSkeleton: React.FC = () => (
  <div className="glass-panel overflow-hidden p-5 flex flex-col h-full w-full relative">
    <SkeletonHeader title="Today's Chores" icon="🧹" />
    <div className="flex-1 flex flex-col justify-between min-h-0">
      <div className="relative w-16 h-16 mx-auto mb-4 rounded-full border-4 border-white/5 flex items-center justify-center animate-pulse">
        <div className="h-3 bg-white/5 rounded w-8" />
      </div>
      <div className="space-y-2 overflow-y-hidden flex-1">
        <div className="h-8 bg-white/5 rounded-lg w-full animate-pulse" />
        <div className="h-8 bg-white/5 rounded-lg w-full animate-pulse" />
      </div>
    </div>
  </div>
);

export const UpcomingEventsSkeleton: React.FC = () => (
  <div className="glass-panel overflow-hidden p-5 flex flex-col h-full w-full relative">
    <SkeletonHeader title="Upcoming Events" icon="📅" />
    <div className="flex-1 flex flex-col justify-between min-h-0">
      <div className="space-y-3 overflow-y-hidden">
        <div className="flex items-center gap-3 p-2 bg-white/5 rounded-xl animate-pulse">
          <div className="h-8 w-12 bg-white/5 rounded-lg" />
          <div className="h-4 bg-white/5 rounded w-1/2" />
        </div>
        <div className="flex items-center gap-3 p-2 bg-white/5 rounded-xl animate-pulse">
          <div className="h-8 w-12 bg-white/5 rounded-lg" />
          <div className="h-4 bg-white/5 rounded w-2/3" />
        </div>
      </div>
      <div className="h-4 bg-white/5 rounded w-1/4 animate-pulse mt-4 self-end" />
    </div>
  </div>
);

export const QuickAssistantSkeleton: React.FC = () => (
  <div className="glass-panel overflow-hidden p-5 flex flex-col h-full w-full relative">
    <SkeletonHeader title="Quick Assistant" icon="🤖" />
    <div className="flex-1 flex flex-col justify-between min-h-0">
      <div className="flex-1 flex items-center justify-center py-4">
        <div className="text-center space-y-2">
          <div className="h-4 bg-white/5 rounded w-24 mx-auto animate-pulse" />
          <div className="h-3 bg-white/5 rounded w-36 mx-auto animate-pulse" />
        </div>
      </div>
      <div className="h-10 bg-white/5 rounded-lg w-full animate-pulse mt-2" />
    </div>
  </div>
);

export const DeviceControlSkeleton: React.FC = () => (
  <div className="glass-panel overflow-hidden p-5 flex flex-col h-full w-full relative">
    <SkeletonHeader title="Device Controls" icon="📱" />
    <div className="flex border-b border-white/5 mb-3">
      <div className="flex-1 h-8 bg-white/5 animate-pulse rounded-t" />
      <div className="flex-1 h-8 bg-white/5 animate-pulse rounded-t mx-1" />
      <div className="flex-1 h-8 bg-white/5 animate-pulse rounded-t" />
    </div>
    <div className="flex-1 space-y-2 overflow-y-hidden">
      <div className="h-12 bg-white/5 rounded-xl w-full animate-pulse" />
      <div className="h-12 bg-white/5 rounded-xl w-full animate-pulse" />
      <div className="h-12 bg-white/5 rounded-xl w-full animate-pulse" />
    </div>
  </div>
);

const skeletonMap: Record<WidgetKey, React.ComponentType> = {
  energy_insights: EnergyInsightsSkeleton,
  ambient_timer: AmbientTimerSkeleton,
  quick_notes: QuickNotesSkeleton,
  active_media: ActiveMediaSkeleton,
  chores_progress: ChoresProgressSkeleton,
  upcoming_events: UpcomingEventsSkeleton,
  quick_assistant: QuickAssistantSkeleton,
  device_control: DeviceControlSkeleton,
};

interface WidgetSkeletonSelectorProps {
  widgetKey: WidgetKey;
}

export const WidgetSkeletonSelector: React.FC<WidgetSkeletonSelectorProps> = ({ widgetKey }) => {
  const SkeletonComponent = skeletonMap[widgetKey];
  if (!SkeletonComponent) {
    return (
      <div className="glass-card h-full p-5 flex items-center justify-center">
        <div className="animate-pulse text-sm text-slate-500">Loading...</div>
      </div>
    );
  }
  return <SkeletonComponent />;
};
