import { useState, useEffect, useRef } from 'react';
import { MapPin, ChevronLeft, ChevronRight, Navigation } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';
import { useLocation } from '../../context/LocationContext.types';
import { api } from '../../services/api';

interface Room {
  id: string;
  name: string;
  confidence: number;
}

interface PresenceData {
  presence?: {
    room: string;
    confidence: number;
  };
}

interface HaloBannerProps {
  rooms?: Room[];
  userId?: string;
}

const HaloBanner = ({ rooms, userId }: HaloBannerProps) => {
  const { trigger } = useHaptics();
  const locationCtx = useLocation();
  const hasLocation = locationCtx?.latitude !== null;
  const [presenceRoom, setPresenceRoom] = useState<Room | null>(null);
  const [loading, setLoading] = useState(false);
  const loadingRef = useRef(false);

  useEffect(() => {
    if (!userId) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    api.getUserPresence(userId)
      .then((data: PresenceData) => {
        if (data?.presence) {
          setPresenceRoom({
            id: data.presence.room,
            name: data.presence.room.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
            confidence: data.presence.confidence,
          });
        }
      })
      .catch(() => setPresenceRoom(null))
      .finally(() => {
        setLoading(false);
        loadingRef.current = false;
      });
  }, [userId]);

  const displayRooms = rooms || (presenceRoom ? [presenceRoom] : []);
  const sortedRooms = [...displayRooms].sort((a, b) => b.confidence - a.confidence);
  const bestRoomId = sortedRooms[0]?.id;
  const defaultIdx = displayRooms.findIndex((r) => r.id === bestRoomId);

  const [currentIndex, setCurrentIndex] = useState(() => {
    return defaultIdx >= 0 ? defaultIdx : 0;
  });

  const currentRoom = displayRooms[currentIndex];

  const navigate = (direction: 'prev' | 'next') => {
    trigger('light');
    setCurrentIndex((prev) => {
      if (displayRooms.length === 0) return prev;
      if (direction === 'prev') return prev === 0 ? displayRooms.length - 1 : prev - 1;
      return prev === displayRooms.length - 1 ? 0 : prev + 1;
    });
  };

  if (!currentRoom && !hasLocation && !loading) return null;

  return (
    <div className="glass-panel border border-purple-500/20 bg-purple-500/5 px-4 py-3 flex items-center justify-between">
      {displayRooms.length > 1 ? (
        <button
          onClick={() => navigate('prev')}
          className="p-1 text-slate-400 hover:text-white transition-colors"
          aria-label="Previous room"
        >
          <ChevronLeft size={16} />
        </button>
      ) : (
        <div className="w-4" />
      )}

      <div className="flex items-center gap-2 flex-1 justify-center">
        {loading ? (
          <p className="text-sm text-slate-400">Detecting location...</p>
        ) : currentRoom ? (
          <>
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
          </>
        ) : hasLocation ? (
          <>
            <Navigation size={16} className="text-purple-400 shrink-0" />
            <p className="text-sm text-slate-300">
              Location tracking active
            </p>
          </>
        ) : null}
      </div>

      {displayRooms.length > 1 ? (
        <button
          onClick={() => navigate('next')}
          className="p-1 text-slate-400 hover:text-white transition-colors"
          aria-label="Next room"
        >
          <ChevronRight size={16} />
        </button>
      ) : (
        <div className="w-4" />
      )}
    </div>
  );
};

export default HaloBanner;
