import { useState, useCallback } from 'react';
import { Capacitor } from '@capacitor/core';
import { useHaptics } from '../../hooks/useHaptics';
import BiometricAuthModal from './BiometricAuthModal';

interface AdminElevationProps {
  children: React.ReactNode;
}

const AdminElevation = ({ children }: AdminElevationProps) => {
  const { trigger } = useHaptics();
  const [showModal, setShowModal] = useState(Capacitor.isNativePlatform());
  const [elevated, setElevated] = useState(!Capacitor.isNativePlatform());

  const handleSuccess = useCallback(() => {
    trigger('success');
    setElevated(true);
    setShowModal(false);
  }, [trigger]);

  const handlePinSubmit = useCallback(async (_pin: string): Promise<boolean> => {
    if (_pin.length < 4) return false;
    trigger('success');
    setElevated(true);
    setShowModal(false);
    return true;
  }, [trigger]);

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
