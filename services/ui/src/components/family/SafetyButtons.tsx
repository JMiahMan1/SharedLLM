import { useState } from 'react';
import { Loader2, Navigation, Siren } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import Modal from '../ui/Modal';

/**
 * "On my way" and SOS for the family hub header.
 *
 * On my way tells everyone else you are heading home, with the drive time by
 * road when the server has your position. SOS is an urgent notice with your
 * last position that nobody can switch off, so it always asks first. The
 * server takes one SOS a minute and one On my way every five minutes per
 * person; sooner, it answers 429 with when it can be sent again, shown as is.
 */
/** The server's answer to a repeat within its cooldown (429). */
function isTooSoon(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 429;
}

export default function SafetyButtons() {
  const [busy, setBusy] = useState<'way' | 'sos' | null>(null);
  const [confirming, setConfirming] = useState(false);

  const onMyWay = async () => {
    setBusy('way');
    try {
      const res = await api.sendOnMyWay();
      const minutes = res.eta_s ? Math.max(1, Math.round(res.eta_s / 60)) : null;
      toast.success(minutes ? `Family told: home in about ${minutes} min` : 'Family told you are on the way');
    } catch (err) {
      if (isTooSoon(err)) toast(errorText(err));
      else toast.error(errorText(err, 'Could not tell the family'));
    } finally {
      setBusy(null);
    }
  };

  const sendSos = async () => {
    setBusy('sos');
    try {
      const res = await api.sendSos();
      setConfirming(false);
      toast.success(
        res.located ? `SOS sent to ${res.sent} with your location` : `SOS sent to ${res.sent}. Your location is not known.`,
      );
    } catch (err) {
      if (isTooSoon(err)) {
        // The last SOS already went out; this one was not needed.
        setConfirming(false);
        toast(errorText(err));
      } else {
        toast.error(errorText(err, 'The SOS did not go out. Call someone.'));
      }
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex shrink-0 items-center gap-2" data-testid="family-safety">
      <button
        type="button"
        onClick={onMyWay}
        disabled={busy !== null}
        className="flex min-h-11 items-center gap-1.5 rounded-full border border-white/10 bg-slate-900/80 px-3 text-[13px] font-medium text-slate-200 hover:border-purple-400/40 disabled:opacity-50"
      >
        {busy === 'way' ? <Loader2 size={14} className="animate-spin" /> : <Navigation size={14} />} On my way
      </button>
      <button
        type="button"
        onClick={() => setConfirming(true)}
        disabled={busy !== null}
        className="flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-full bg-rose-600 px-3 text-[13px] font-bold text-white hover:bg-rose-500 disabled:opacity-50"
      >
        <Siren size={14} /> SOS
      </button>
      <Modal isOpen={confirming} onClose={() => busy === null && setConfirming(false)} title="Send an SOS?" size="sm">
        <p className="text-sm text-slate-300">
          Everyone else in the family gets an urgent alert with your last known location, even if they turned
          family alerts off.
        </p>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={() => setConfirming(false)}
            disabled={busy !== null}
            className="min-h-11 rounded-xl border border-white/10 px-4 text-sm text-slate-200 hover:bg-white/5"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={sendSos}
            disabled={busy !== null}
            className="flex min-h-11 items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 text-sm font-bold text-white hover:bg-rose-500 disabled:opacity-60"
          >
            {busy === 'sos' ? <Loader2 size={16} className="animate-spin" /> : <Siren size={16} />} Send SOS
          </button>
        </div>
      </Modal>
    </div>
  );
}
