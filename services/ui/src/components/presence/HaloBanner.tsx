import { useState } from 'react';
import { MapPin, ChevronLeft, ChevronRight } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';

interface Room {
  id: string;
  name: string;
  confidence: number;
}

interface HaloBannerProps {
  rooms?: Room[];
}

const MOCK_ROOMS: Room[] = [
  { id: 'living_room', name: 'Living Room', confidence: 0.92 },
  { id: 'kitchen', name: 'Kitchen', confidence: 0.65 },
  { id: 'master_bed', name: 'Master Bedroom', confidence: 0.41 },
];

const HaloBanner = ({ rooms = MOCK_ROOMS }: HaloBannerProps) => {
  const { trigger } = useHaptics();

  const sortedRooms = [...rooms].sort((a, b) => b.confidence - a.confidence);
  const bestRoomId = sortedRooms[0]?.id;
  const defaultIdx = rooms.findIndex((r) => r.id === bestRoomId);

  const [currentIndex, setCurrentIndex] = useState(defaultIdx >= 0 ? defaultIdx : 0);

  const currentRoom = rooms[currentIndex];

  const navigate = (direction: 'prev' | 'next') => {
    trigger('light');
    setCurrentIndex((prev) => {
      if (direction === 'prev') return prev === 0 ? rooms.length - 1 : prev - 1;
      return prev === rooms.length - 1 ? 0 : prev + 1;
    });
  };

  if (!currentRoom) return null;

  return (
    <div className="glass-panel border border-purple-500/20 bg-purple-500/5 px-4 py-3 flex items-center justify-between">
      <button
        onClick={() => navigate('prev')}
        className="p-1 text-slate-400 hover:text-white transition-colors"
        aria-label="Previous room"
      >
        <ChevronLeft size={16} />
      </button>

      <div className="flex items-center gap-2 flex-1 justify-center">
        <MapPin size={16} className="text-purple-400 shrink-0" />
        <p className="text-sm text-slate-300">
          You are in the{' '}
          <span className="font-semibold text-white">{currentRoom.name}</span>
        </p>
        {currentRoom.confidence > 0.8 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-500/20 text-green-400 border border-green-500/30">
            BLE
          </span>
        )}
      </div>

      <button
        onClick={() => navigate('next')}
        className="p-1 text-slate-400 hover:text-white transition-colors"
        aria-label="Next room"
      >
        <ChevronRight size={16} />
      </button>
    </div>
  );
};

export default HaloBanner;
