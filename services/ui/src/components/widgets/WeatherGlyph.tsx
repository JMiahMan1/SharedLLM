import { createElement } from 'react';
import { ICON_TONE, weatherIcon, type WeatherIcon } from '../../lib/weather';

/** The icon for a forecast condition, tinted by kind; a clear night is a moon. */
export function WeatherGlyph({
  icon, isDay = true, size = 16, label,
}: { icon: WeatherIcon; isDay?: boolean; size?: number; label?: string }) {
  return createElement(weatherIcon(icon, isDay), {
    size,
    className: `shrink-0 ${ICON_TONE[icon] ?? 'text-slate-300'}`,
    ...(label ? { 'aria-label': label, role: 'img' } : { 'aria-hidden': true }),
  });
}
