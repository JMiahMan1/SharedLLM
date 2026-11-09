import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Camera, MapPin, Navigation } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { zonePlaces } from '../../lib/wanderQueries';
import { statusQuery } from '../../lib/memberPage';
import MemberCard from '../member/MemberCard';
import type { ScreenRules } from '../../types/api';

interface GeofenceAlertsCardProps {
  username: string;
  name: string;
}

/**
 * Family Link / Bark Batch 7: Place arrival/departure geofence alerts
 * and on-device photo & video safety monitoring for a child.
 */
export default function GeofenceAlertsCard({ username, name }: GeofenceAlertsCardProps) {
  const queryClient = useQueryClient();

  const { data: status, isLoading: statusLoading } = useQuery(statusQuery(username));
  const { data: zonesData, isLoading: zonesLoading } = useQuery({
    queryKey: ['geo-zones'],
    queryFn: () => api.getGeoZones(),
    staleTime: 5 * 60 * 1000,
  });

  const places = zonePlaces(zonesData);
  const rules = status?.rules || {};
  const geofenceAlerts = rules.geofence_alerts || {};
  const photoScanEnabled = rules.photo_scan !== false;

  const saveMutation = useMutation({
    mutationFn: (updated: Partial<ScreenRules>) => {
      const full: ScreenRules = { ...rules, ...updated };
      delete full.paused;
      delete full.paused_until;
      return api.setScreenRules(username, full);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
      void queryClient.invalidateQueries({ queryKey: ['screen-time-rules', username] });
      toast.success('Safety settings updated');
    },
    onError: () => {
      toast.error('Could not save safety settings');
    },
  });

  const toggleGeofence = (zoneId: string, kind: 'arrive' | 'depart') => {
    const current = geofenceAlerts[zoneId] || {};
    const nextAlerts = {
      ...geofenceAlerts,
      [zoneId]: {
        ...current,
        [kind]: !current[kind],
      },
    };
    saveMutation.mutate({ geofence_alerts: nextAlerts });
  };

  const togglePhotoScan = () => {
    saveMutation.mutate({ photo_scan: !photoScanEnabled });
  };

  return (
    <MemberCard
      title="Places & Photo Safety"
      icon={MapPin}
      tone="text-emerald-300"
      testId="guardian-geofences-card"
      isLoading={statusLoading || zonesLoading}
    >
      <div className="space-y-4 text-xs">
        {/* Bark-style photo & video safety scanning toggle */}
        <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5 space-y-2">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-start gap-2.5 min-w-0">
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-emerald-400/20 bg-emerald-400/10">
                <Camera size={15} className="text-emerald-300" />
              </span>
              <div className="min-w-0 space-y-1">
                <p className="font-semibold text-white">Photo &amp; Video Safety Scan</p>
                <p className="text-[11px] leading-relaxed text-slate-400">
                  Scans new photos locally using household AI for safety concerns without uploading private photos.
                </p>
                {/* The setting is kept, but no phone sends photos yet: saying
                    "Active" told the parents something was watching that was not. */}
                <p className="text-[11px] text-amber-300/90" data-testid="photo-scan-not-yet">
                  Not scanning yet: the phone app does not send new photos in this version.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={togglePhotoScan}
              disabled={saveMutation.isPending}
              data-testid="toggle-photo-scan"
              className={`min-h-11 shrink-0 rounded-xl px-3.5 text-xs font-semibold transition-colors ${
                photoScanEnabled
                  ? 'border border-emerald-500/40 bg-emerald-500/20 text-emerald-200'
                  : 'border border-white/10 bg-white/5 text-slate-400 hover:text-white'
              }`}
            >
              {photoScanEnabled ? 'On' : 'Off'}
            </button>
          </div>
        </div>

        {/* Places geofence arrival & departure alerts */}
        <div className="space-y-2.5">
          <div className="flex items-center gap-1.5 font-semibold text-slate-200">
            <Navigation size={13} className="text-sky-300" />
            <span>Place Arrival &amp; Departure Alerts for {name}</span>
          </div>

          {places.length === 0 ? (
            <p className="rounded-xl border border-white/10 bg-white/[0.02] p-3 text-slate-400">
              No places configured in Home Assistant yet (e.g. Home, School).
            </p>
          ) : (
            <div className="space-y-2">
              {places.map((place) => {
                const cfg = geofenceAlerts[place.id] || {};
                const arriveOn = Boolean(cfg.arrive);
                const departOn = Boolean(cfg.depart);

                return (
                  <div
                    key={place.id}
                    className="flex flex-wrap items-center justify-between gap-2.5 rounded-xl border border-white/10 bg-white/[0.03] p-3"
                    data-testid={`geofence-row-${place.id}`}
                  >
                    <div className="min-w-0 flex-1 pr-2">
                      <p className="truncate font-semibold text-white">{place.name}</p>
                      <p className="truncate text-[10px] text-slate-400">{place.id}</p>
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        type="button"
                        onClick={() => toggleGeofence(place.id, 'arrive')}
                        disabled={saveMutation.isPending}
                        data-testid={`geofence-arrive-${place.id}`}
                        className={`min-h-11 rounded-xl px-3 text-xs font-semibold transition-colors ${
                          arriveOn
                            ? 'border border-emerald-400/40 bg-emerald-400/20 text-emerald-200'
                            : 'border border-white/10 bg-white/5 text-slate-400 hover:text-white'
                        }`}
                        title="Alert parents on arrival"
                      >
                        Arrive
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleGeofence(place.id, 'depart')}
                        disabled={saveMutation.isPending}
                        data-testid={`geofence-depart-${place.id}`}
                        className={`min-h-11 rounded-xl px-3 text-xs font-semibold transition-colors ${
                          departOn
                            ? 'border border-sky-400/40 bg-sky-400/20 text-sky-200'
                            : 'border border-white/10 bg-white/5 text-slate-400 hover:text-white'
                        }`}
                        title="Alert parents on departure (with ETA home)"
                      >
                        Depart
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </MemberCard>
  );
}
