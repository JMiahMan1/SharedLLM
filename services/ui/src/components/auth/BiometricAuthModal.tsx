import { useState, useEffect } from 'react';
import { useBiometricAuth } from '../../hooks/useBiometricAuth';
import { useHaptics } from '../../hooks/useHaptics';
import { Fingerprint, X } from 'lucide-react';

interface BiometricAuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  title?: string;
  reason?: string;
  fallbackPinSubmit?: (pin: string) => Promise<boolean | string>;
}

const BiometricAuthModal = ({
  isOpen,
  onClose,
  onSuccess,
  title = 'Authentication Required',
  reason = 'Verify your identity to continue',
  fallbackPinSubmit,
}: BiometricAuthModalProps) => {
  const { isNative, isAvailable, checkAvailability, authenticate } = useBiometricAuth();
  const { trigger } = useHaptics();
  const [showPinFallback, setShowPinFallback] = useState(false);
  const [pin, setPin] = useState('');
  const [error, setError] = useState('');
  const [authenticating, setAuthenticating] = useState(false);

  useEffect(() => {
    if (isOpen && isNative) {
      checkAvailability();
    }
  }, [isOpen, isNative, checkAvailability]);

  const handleBiometricAuth = async () => {
    setAuthenticating(true);
    trigger('medium');

    const result = await authenticate(reason);
    setAuthenticating(false);

    if (result.success) {
      trigger('success');
      onSuccess();
      onClose();
    } else {
      trigger('error');
      setError(result.error || 'Authentication failed');
      setShowPinFallback(true);
    }
  };

  const handlePinSubmit = async () => {
    if (!fallbackPinSubmit) return;

    if (pin.length < 4) {
      setError('PIN must be at least 4 digits');
      return;
    }

    setAuthenticating(true);
    const result = await fallbackPinSubmit(pin);
    setAuthenticating(false);

    if (result === true) {
      trigger('success');
      onSuccess();
      onClose();
    } else {
      trigger('error');
      setError(typeof result === 'string' ? result : 'Invalid PIN');
      setPin('');
    }
  };

  const handlePinDigit = (digit: string) => {
    if (pin.length < 4) {
      setPin((prev) => prev + digit);
      trigger('light');
    }
  };

  const handlePinDelete = () => {
    setPin((prev) => prev.slice(0, -1));
    trigger('light');
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xl">
      <div className="glass-panel w-full max-w-sm mx-4 p-6 rounded-2xl relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-400 hover:text-white"
        >
          <X size={20} />
        </button>

        <h2 className="text-xl font-bold text-white text-center mb-2">{title}</h2>
        <p className="text-sm text-slate-400 text-center mb-6">{reason}</p>

        {isNative && isAvailable && !showPinFallback ? (
          <div className="flex flex-col items-center gap-4">
            <button
              onClick={handleBiometricAuth}
              disabled={authenticating}
              className="w-20 h-20 rounded-full bg-purple-500/20 border border-purple-500/30 flex items-center justify-center hover:bg-purple-500/30 transition-colors disabled:opacity-50"
            >
              <Fingerprint size={40} className="text-purple-400" />
            </button>
            <p className="text-sm text-slate-400">Tap to authenticate</p>
            <button
              onClick={() => setShowPinFallback(true)}
              className="text-xs text-purple-400 hover:text-purple-300 mt-2"
            >
              Use PIN instead
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-4">
            <div className="flex gap-3 mb-4">
              {[0, 1, 2, 3].map((i) => (
                <div
                  key={i}
                  className={`w-3 h-3 rounded-full transition-colors ${
                    i < pin.length ? 'bg-purple-400' : 'bg-slate-600'
                  }`}
                />
              ))}
            </div>

            <div className="grid grid-cols-3 gap-3 w-full max-w-[240px]">
              {['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', 'del'].map((key) => (
                <button
                  key={key}
                  onClick={() => {
                    if (key === 'del') handlePinDelete();
                    else if (key !== '') handlePinDigit(key);
                  }}
                  disabled={key === ''}
                  className="h-14 rounded-xl bg-white/5 border border-white/10 text-white font-medium hover:bg-white/10 transition-colors disabled:invisible"
                >
                  {key === 'del' ? '⌫' : key}
                </button>
              ))}
            </div>

            {pin.length === 4 && fallbackPinSubmit && (
              <button
                onClick={handlePinSubmit}
                disabled={authenticating}
                className="w-full py-3 rounded-xl bg-purple-500/30 border border-purple-500/30 text-white font-medium hover:bg-purple-500/40 transition-colors disabled:opacity-50 mt-2"
              >
                {authenticating ? 'Verifying...' : 'Submit'}
              </button>
            )}
          </div>
        )}

        {error && (
          <p className="text-sm text-red-400 text-center mt-4">{error}</p>
        )}
      </div>
    </div>
  );
};

export default BiometricAuthModal;
