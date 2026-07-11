export type IntegrationType = 'nextcloud' | 'skylight' | 'ical';

interface IntegrationMeta {
  label: string;
  color: string; // warm accent used for dots/borders
  chip: string; // tailwind classes for chips
}

// Warm "paper-planner" accents (mirrors OpenSkyLight's ember/sun palette)
export const INTEGRATION_META: Record<IntegrationType, IntegrationMeta> = {
  nextcloud: {
    label: 'Nextcloud',
    color: '#d95b3a',
    chip: 'border-ember/40 bg-ember-soft text-ember-deep',
  },
  skylight: {
    label: 'Skylight',
    color: '#c77d9e',
    chip: 'border-rose-300/40 bg-rose-100 text-rose-700',
  },
  ical: {
    label: 'iCal',
    color: '#caa15a',
    chip: 'border-amber-400/40 bg-amber-100 text-amber-700',
  },
};

export const integrationMeta = (type?: string): IntegrationMeta =>
  INTEGRATION_META[(type as IntegrationType)] ?? {
    label: type || 'Calendar',
    color: '#a89f8d',
    chip: 'border-stone-300 text-stone-500',
  };
