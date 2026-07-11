export type IntegrationType = 'nextcloud' | 'skylight' | 'ical';

interface IntegrationMeta {
  label: string;
  color: string; // hex used for dots/borders
  chip: string; // tailwind classes for chips
}

export const INTEGRATION_META: Record<IntegrationType, IntegrationMeta> = {
  nextcloud: {
    label: 'Nextcloud',
    color: '#f8a33b',
    chip: 'border-orange-400/40 bg-orange-500/10 text-orange-300',
  },
  skylight: {
    label: 'Skylight',
    color: '#7c5cff',
    chip: 'border-violet-400/40 bg-violet-500/10 text-violet-300',
  },
  ical: {
    label: 'iCal',
    color: '#22c55e',
    chip: 'border-green-400/40 bg-green-500/10 text-green-300',
  },
};

export const integrationMeta = (type?: string): IntegrationMeta =>
  INTEGRATION_META[(type as IntegrationType)] ?? {
    label: type || 'Calendar',
    color: '#64748b',
    chip: 'border-slate-400/40 bg-slate-500/10 text-slate-300',
  };
