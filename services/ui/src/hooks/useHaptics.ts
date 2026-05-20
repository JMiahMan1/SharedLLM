import { Capacitor } from '@capacitor/core';
import { Haptics, ImpactStyle } from '@capacitor/haptics';

export function useHaptics() {
  const trigger = async (style: 'light' | 'medium' | 'heavy' | 'success' | 'warning' | 'error' = 'light') => {
    if (!Capacitor.isNativePlatform()) return;

    const styleMap: Record<string, ImpactStyle> = {
      light: ImpactStyle.Light,
      medium: ImpactStyle.Medium,
      heavy: ImpactStyle.Heavy,
      success: ImpactStyle.Medium,
      warning: ImpactStyle.Heavy,
      error: ImpactStyle.Heavy,
    };

    try {
      await Haptics.impact({ style: styleMap[style] });
    } catch {
      // Haptics unavailable, silently ignore
    }
  };

  return { trigger };
}
