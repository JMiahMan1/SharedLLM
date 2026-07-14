import { useState, useCallback } from 'react';
import { Capacitor } from '@capacitor/core';
import { BiometricAuth } from '@aparajita/capacitor-biometric-auth';

export interface BiometricResult {
  success: boolean;
  error?: string;
}

export function useBiometricAuth() {
  const [isAvailable, setIsAvailable] = useState<boolean | null>(null);

  const checkAvailability = useCallback(async (): Promise<boolean> => {
    if (!Capacitor.isNativePlatform()) {
      setIsAvailable(false);
      return false;
    }

    try {
      const result = await BiometricAuth.checkBiometry();
      const available = result.isAvailable;
      setIsAvailable(available);
      return available;
    } catch {
      setIsAvailable(false);
      return false;
    }
  }, []);

  const authenticate = useCallback(async (reason: string = 'Authenticate to continue'): Promise<BiometricResult> => {
    if (!Capacitor.isNativePlatform()) {
      return { success: false, error: 'Biometrics not available on web' };
    }

    try {
      await BiometricAuth.authenticate({
        reason,
        androidTitle: 'Jarvis OS',
        androidSubtitle: reason,
        allowDeviceCredential: true,
      });
      return { success: true };
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Authentication failed';
      return { success: false, error: message };
    }
  }, []);

  return {
    isAvailable,
    checkAvailability,
    authenticate,
    isNative: Capacitor.isNativePlatform(),
  };
}
