import { useHaptics } from '../../hooks/useHaptics';

interface ToggleProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label?: string;
  ariaLabel?: string;
}

/**
 * Shared pill switch used across Settings/sensor panels.
 * Knob is vertically centered via top-1/2 -translate-y-1/2 so the track
 * height (h-6/h-7) never leaves the thumb visually off-center.
 */
export function Toggle({ checked, onChange, disabled = false, label, ariaLabel }: ToggleProps) {
  const { trigger } = useHaptics();

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel || label}
      disabled={disabled}
      onClick={() => {
        if (disabled) return;
        trigger('light');
        onChange(!checked);
      }}
      className={`shrink-0 w-11 h-6 rounded-full relative transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/60 ${
        checked ? 'bg-purple-500' : 'bg-slate-600'
      } ${disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
    >
      <span
        className={`absolute top-1/2 -translate-y-1/2 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-5' : 'translate-x-0'
        }`}
      />
    </button>
  );
}

export default Toggle;
