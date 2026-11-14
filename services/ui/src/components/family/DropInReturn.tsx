import { useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useCall } from '../../context/CallContext';

/**
 * A Drop In opens Family > Calls to join. When it ends, the phone goes back to
 * the chat with the people it was talking to (a call's room is that chat's
 * conversation), not the call list. Someone who moved to another page during
 * the call stays where they are.
 */
export default function DropInReturn() {
  const { call } = useCall();
  const navigate = useNavigate();
  const location = useLocation();
  const dropInRoom = useRef<string | null>(null);
  const live = Boolean(call && (call.status === 'connecting' || call.status === 'connected'));

  useEffect(() => {
    if (live) {
      dropInRoom.current = call?.dropIn ? call.room : null;
      return;
    }
    const room = dropInRoom.current;
    if (!room) return;
    dropInRoom.current = null;
    if (location.pathname === '/family') navigate(`/family?chat=${encodeURIComponent(room)}`, { replace: true });
  }, [live, call, location.pathname, navigate]);

  return null;
}
