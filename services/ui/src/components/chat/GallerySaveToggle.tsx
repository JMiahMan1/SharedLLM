/**
 * "Save to gallery" for a picture, a clip or a voice note about to be sent.
 *
 * Off, always, until somebody turns it on. The Encrypted Gallery is shared
 * between the two parents, so nothing joins it by accident: a GIF from a
 * keyboard goes to the conversation it was sent in, not to a shelf the other
 * parent browses. Anything can still be added later from its bubble, which is
 * where a parent who only realised afterwards belongs.
 *
 * A checkbox, not a switch: it reads the same to a screen reader, takes a
 * proper 44 px target, and does not animate under a thumb.
 */
import { ImagePlus } from 'lucide-react';

export function GallerySaveToggle({ checked, onChange, what, testId, disabled }: {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** What is being kept, for the label: "this voice note". */
  what?: string;
  testId?: string;
  disabled?: boolean;
}) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center gap-2.5 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-[11px] text-slate-200">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        data-testid={testId}
        className="h-5 w-5 shrink-0 accent-rose-500"
      />
      <ImagePlus size={14} className="shrink-0 opacity-80" aria-hidden />
      <span className="min-w-0">
        <span className="font-semibold">Save to gallery</span>
        <span className="block opacity-70">
          {what ? <>Keeps {what} in the shared Encrypted Gallery.</> : 'Keeps this in the shared Encrypted Gallery.'}
        </span>
      </span>
    </label>
  );
}
