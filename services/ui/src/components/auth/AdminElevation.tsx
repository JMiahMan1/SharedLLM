import { useState, useCallback, useEffect } from 'react';
import { Capacitor } from '@capacitor/core';
import { useHaptics } from '../../hooks/useHaptics';
import BiometricAuthModal from './BiometricAuthModal';
import { verifyAdminPin, isAdminPinSet, setAdminPin } from '../../lib/adminPin';
import { adminElevated, markAdminElevated } from '../../lib/adminElevation';

interface AdminElevationProps {
  children: React.ReactNode;
}

const AdminElevation = ({ children }: AdminElevationProps) => {
  const { trigger } = useHaptics();
  // A check passed in the last ADMIN_ELEVATION_MS (this app session) still holds.
  const [elevated, setElevated] = useState(() => !Capacitor.isNativePlatform() || adminElevated());
  const [showModal, setShowModal] = useState(() => Capacitor.isNativePlatform() && !adminElevated());

  const handleSuccess = useCallback(() => {
    trigger('success');
    markAdminElevated();
    setElevated(true);
    setShowModal(false);
  }, [trigger]);

  const handlePinSubmit = useCallback(async (_pin: string): Promise<boolean | string> => {
    if (_pin.length < 4) return 'PIN must be at least 4 digits';
    if (!isAdminPinSet()) {
      await setAdminPin(_pin);
      trigger('success');
      markAdminElevated();
      setElevated(true);
      setShowModal(false);
      return true;
    }
    const ok = await verifyAdminPin(_pin);
    if (ok) {
      trigger('success');
      markAdminElevated();
      setElevated(true);
      setShowModal(false);
      return true;
    }
    return 'Incorrect PIN';
  }, [trigger]);

  // While an admin page is open the check keeps renewing, so the reload a web
  // update does in the background never asks again; closing the app ends it.
  useEffect(() => {
    if (!elevated || !Capacitor.isNativePlatform()) return undefined;
    markAdminElevated();
    const timer = window.setInterval(() => markAdminElevated(), 60_000);
    return () => window.clearInterval(timer);
  }, [elevated]);

  if (!Capacitor.isNativePlatform()) return <>{children}</>;

  if (!elevated) {
    return (
      <BiometricAuthModal
        isOpen={showModal}
        onClose={() => window.history.back()}
        onSuccess={handleSuccess}
        title="Admin Access Required"
        reason="Verify admin identity to access system operations"
        fallbackPinSubmit={handlePinSubmit}
      />
    );
  }

  return <>{children}</>;
};

export default AdminElevation;
