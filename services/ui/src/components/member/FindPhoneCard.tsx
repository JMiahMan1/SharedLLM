import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { BellRing } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import MemberCard from './MemberCard';

/**
 * Ring a child's phone (a parent's Find my phone). It sends the same notice the
 * watch's button does, so the phone rings on its alarm stream even on silent;
 * a short cooldown per person keeps it from nagging.
 */
export default function FindPhoneCard({ username, name }: { username: string; name: string }) {
  const [message, setMessage] = useState<string | null>(null);
  const ring = useMutation({
    mutationFn: () => api.ringMemberPhone(username),
    onSuccess: (data) => {
      setMessage(data.message || 'The phone is ringing.');
      toast.success(`${name}'s phone is ringing`);
    },
    onError: (error: Error) => {
      const text = error.message || 'Could not reach that phone';
      setMessage(text);
      toast.error(text);
    },
  });

  return (
    <MemberCard title="Find phone" icon={BellRing} tone="text-amber-300" testId="member-find-phone">
      <p className="text-xs text-slate-400">
        Rings {name}&apos;s phone on its alarm stream, even on silent. The phone shows a Stop button.
      </p>
      <button
        type="button"
        onClick={() => ring.mutate()}
        disabled={ring.isPending}
        data-testid="member-ring-phone"
        className="glass-button mt-2 min-h-11 px-4 text-xs font-semibold disabled:opacity-50"
      >
        {ring.isPending ? 'Ringing...' : `Ring ${name}'s phone`}
      </button>
      {message && (
        <p className="mt-2 text-[11px] text-slate-400" data-testid="member-find-phone-message">
          {message}
        </p>
      )}
    </MemberCard>
  );
}
