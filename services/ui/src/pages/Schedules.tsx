import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Loader2, Plane, Plus, Zap } from 'lucide-react';
import ThermostatCard from '../components/schedules/ThermostatCard';
import ProgramEditor, { SlotEditor } from '../components/schedules/ProgramEditor';
import ClimateSettingsPanel from '../components/schedules/ClimateSettingsPanel';
import OtherSchedules from '../components/schedules/OtherSchedules';
import { Chip } from '../components/automations/FormBits';
import { BUTTON, PRIMARY } from '../components/automations/styles';
import { errorText } from '../lib/errorText';
import { getCatalog } from '../lib/automations';
import {
  blankProgram, defaultSetpoint, getClimate, weekdaysText,
  type Program,
} from '../lib/schedules';

const CLIMATE_KEY = ['homehub-climate'];

/**
 * Schedules: thermostat programs (each thermostat's week, what it does now and
 * next, holds), away and energy settings, and the other scheduler schedules.
 * Every thermostat, program and schedule comes from the hub; a family member
 * sees the thermostats they may see and can hold or resume them.
 */
export default function Schedules() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<'thermostats' | 'other'>('thermostats');
  const climate = useQuery({ queryKey: CLIMATE_KEY, queryFn: getClimate, refetchInterval: () => (document.hidden ? false : 30_000) });
  const canEdit = !!climate.data?.can_edit;
  // The catalog fills the people, power sensor and service pickers; only editors need it.
  const catalog = useQuery({ queryKey: ['homehub-automation-catalog'], queryFn: getCatalog, staleTime: 5 * 60_000, retry: false, enabled: canEdit });
  const [program, setProgram] = useState<Partial<Program> | null>(null);
  const [slot, setSlot] = useState<{ program: Program; slot: number } | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: CLIMATE_KEY });
  const nowIso = climate.data?.now;
  const now = useMemo(() => (nowIso ? new Date(nowIso) : new Date()), [nowIso]);
  const thermostats = climate.data?.thermostats ?? [];
  const programs = climate.data?.programs ?? [];

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 pb-24">
      <div className="flex items-center gap-2">
        <CalendarClock className="text-sky-400" size={22} aria-hidden />
        <h1 className="text-2xl font-bold text-white">Schedules</h1>
      </div>
      <div className="flex gap-1.5" role="tablist" aria-label="Schedules">
        <Chip active={tab === 'thermostats'} onClick={() => setTab('thermostats')}>Thermostats</Chip>
        <Chip active={tab === 'other'} onClick={() => setTab('other')}>Other schedules</Chip>
      </div>

      {tab === 'other' ? <OtherSchedules catalog={catalog.data} /> : climate.isLoading ? (
        <div className="flex justify-center py-10" role="status" aria-label="Loading"><Loader2 className="animate-spin text-slate-500" /></div>
      ) : climate.isError ? (
        <div className="glass-panel rounded-2xl p-6 text-center">
          <p className="text-sm text-red-300">{errorText(climate.error, 'Could not reach the hub')}</p>
          <button type="button" className={`${BUTTON} mt-2`} onClick={() => void climate.refetch()}>Try again</button>
        </div>
      ) : climate.data && (
        <>
          {(climate.data.away?.active || climate.data.energy?.in_window) && (
            <div className="flex flex-wrap gap-2 text-xs">
              {climate.data.away?.active && (
                <span className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-3 text-indigo-100">
                  <Plane size={14} aria-hidden /> Away{climate.data.away.reason ? `: ${climate.data.away.reason}` : ''}
                </span>
              )}
              {climate.data.energy?.in_window && (
                <span className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 text-amber-100">
                  <Zap size={14} aria-hidden /> {climate.data.energy.window || 'Rate window'}
                  {typeof climate.data.energy.power_kw === 'number' ? ` · ${climate.data.energy.power_kw.toFixed(1)} kW` : ''}
                  {climate.data.energy.shed.length ? ` · ${climate.data.energy.shed.length} off to save power` : ''}
                  {climate.data.energy.comforted?.length ? ` · ${climate.data.energy.comforted.length} back on for comfort` : ''}
                </span>
              )}
            </div>
          )}

          {canEdit && (
            <div className="flex flex-wrap gap-2">
              <button type="button" className={PRIMARY} disabled={!thermostats.length}
                onClick={() => setProgram(blankProgram(thermostats.slice(0, 1).map((t) => t.entity_id), defaultSetpoint(thermostats[0])))}>
                <Plus size={14} /> New program
              </button>
              <button type="button" className={BUTTON} aria-expanded={showSettings} onClick={() => setShowSettings((v) => !v)}>
                Away and energy
              </button>
            </div>
          )}
          {canEdit && showSettings && (
            <ClimateSettingsPanel settings={climate.data.settings} thermostats={thermostats} catalog={catalog.data} onSaved={refresh} />
          )}

          {thermostats.length === 0 ? (
            <p className="glass-panel rounded-2xl p-6 text-center text-sm text-slate-400">No thermostats to show.</p>
          ) : (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              {thermostats.map((t) => (
                <ThermostatCard key={t.entity_id} thermostat={t} programs={programs} now={now} onChanged={refresh}
                  onSlot={(p, s) => setSlot({ program: p, slot: s })} />
              ))}
            </div>
          )}

          {programs.length > 0 && (
            <section className="space-y-2">
              <h2 className="text-[10px] font-black uppercase tracking-widest text-slate-400">Programs</h2>
              <ul className="space-y-2">
                {programs.map((p) => (
                  <li key={p.id}>
                    <button type="button" className="glass-panel flex min-h-11 w-full flex-col items-start rounded-2xl border border-white/10 p-3 text-left"
                      onClick={() => setProgram(p)} aria-label={`Open program ${p.name}`}>
                      <span className="text-sm font-semibold text-white">{p.name}{p.enabled ? '' : ' (off)'}</span>
                      <span className="text-xs text-slate-500">
                        {weekdaysText(p.weekdays)} · {p.entity_ids.map((id) => thermostats.find((t) => t.entity_id === id)?.name || id).join(', ')}
                        {p.start_date || p.end_date ? ` · ${p.start_date ?? '...'} to ${p.end_date ?? '...'}` : ''}
                      </span>
                      {p.reason && <span className="text-[11px] text-amber-200">{p.reason}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      {program && (
        <ProgramEditor program={program} thermostats={thermostats} canEdit={canEdit}
          onClose={() => setProgram(null)} onSaved={() => { setProgram(null); refresh(); }} />
      )}
      {slot && (
        <SlotEditor program={slot.program} slot={slot.slot} thermostats={thermostats} canEdit={canEdit}
          onClose={() => setSlot(null)} onSaved={() => { setSlot(null); refresh(); }}
          onEditProgram={() => { setProgram(slot.program); setSlot(null); }} />
      )}
    </div>
  );
}
