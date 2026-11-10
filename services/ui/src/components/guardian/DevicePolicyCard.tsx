import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Cpu, Plus, Shield, Trash2, Wifi } from 'lucide-react';
import Toggle from '../ui/Toggle';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { statusQuery } from '../../lib/memberPage';
import MemberCard from '../member/MemberCard';
import type { CaCertConfig, OsUpdatePolicyType, ScreenRules, WifiNetworkConfig } from '../../types/api';

interface DevicePolicyCardProps {
  username: string;
  name: string;
}

interface PolicyDraft {
  policyType: OsUpdatePolicyType;
  windowStart: string;
  windowEnd: string;
  vpnLockdown: boolean;
  wifiNetworks: WifiNetworkConfig[];
  caCerts: CaCertConfig[];
}

function minToTime(minutes: number | undefined, fallback: string): string {
  if (minutes === undefined || minutes === null || isNaN(minutes)) return fallback;
  const h = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function timeToMin(timeStr: string): number {
  const parts = timeStr.split(':');
  const h = parseInt(parts[0], 10) || 0;
  const m = parseInt(parts[1], 10) || 0;
  return Math.min(1440, Math.max(0, h * 60 + m));
}

function draftFrom(rules: ScreenRules): PolicyDraft {
  const oup = rules.os_update_policy;
  return {
    policyType: (oup?.policy as OsUpdatePolicyType) || 'default',
    windowStart: minToTime(oup?.window_start_min, '02:00'),
    windowEnd: minToTime(oup?.window_end_min, '06:00'),
    vpnLockdown: Boolean(rules.vpn_lockdown || rules.web_filter?.lockdown),
    wifiNetworks: rules.wifi_networks || [],
    caCerts: rules.ca_certs || [],
  };
}

/**
 * MDM Batch 8: OS update policies, always-on VPN lockdown, Wi-Fi provisioning,
 * and CA root certificate management for managed child devices.
 */
export default function DevicePolicyCard({ username, name }: DevicePolicyCardProps) {
  const queryClient = useQueryClient();
  const { data: status, isLoading } = useQuery(statusQuery(username));

  const rules = status?.rules || {};

  const [edited, setDraft] = useState<PolicyDraft | null>(null);
  const draft = edited ?? draftFrom(rules);
  // Nothing here is applied until Save: say so, so an added network is not lost by walking away.
  const dirty = edited !== null && JSON.stringify(edited) !== JSON.stringify(draftFrom(rules));
  const [openPart, setOpenPart] = useState<'wifi' | 'certs' | null>(null);

  // Draft inputs for adding new Wi-Fi
  const [newSsid, setNewSsid] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newSecurity, setNewSecurity] = useState<'open' | 'wpa2' | 'wpa3' | 'wep'>('wpa2');
  const [newHidden, setNewHidden] = useState(false);

  // Draft inputs for adding new CA Cert
  const [newCertAlias, setNewCertAlias] = useState('');
  const [newCertPem, setNewCertPem] = useState('');

  const saveMutation = useMutation({
    mutationFn: () => {
      const full: ScreenRules = {
        ...rules,
        os_update_policy: {
          policy: draft.policyType,
          window_start_min: draft.policyType === 'windowed' ? timeToMin(draft.windowStart) : undefined,
          window_end_min: draft.policyType === 'windowed' ? timeToMin(draft.windowEnd) : undefined,
        },
        vpn_lockdown: draft.vpnLockdown,
        wifi_networks: draft.wifiNetworks,
        ca_certs: draft.caCerts,
      };
      delete full.paused;
      delete full.paused_until;
      return api.setScreenRules(username, full);
    },
    onSuccess: () => {
      setDraft(null);
      void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
      void queryClient.invalidateQueries({ queryKey: ['screen-time-rules', username] });
      toast.success('Device policies saved');
    },
    onError: () => {
      toast.error('Could not save device policies');
    },
  });

  const handleAddWifi = () => {
    const trimmedSsid = newSsid.trim();
    if (!trimmedSsid) {
      toast.error('SSID cannot be empty');
      return;
    }
    if (draft.wifiNetworks.some((w) => w.ssid === trimmedSsid)) {
      toast.error('Wi-Fi network already added');
      return;
    }
    setDraft({
      ...draft,
      wifiNetworks: [
        ...draft.wifiNetworks,
        {
          ssid: trimmedSsid.slice(0, 32),
          password: newPassword,
          security: newSecurity,
          hidden: newHidden,
        },
      ],
    });
    setNewSsid('');
    setNewPassword('');
    setNewHidden(false);
  };

  const handleRemoveWifi = (ssid: string) => {
    setDraft({
      ...draft,
      wifiNetworks: draft.wifiNetworks.filter((w) => w.ssid !== ssid),
    });
  };

  const handleAddCaCert = () => {
    const trimmedAlias = newCertAlias.trim();
    const trimmedPem = newCertPem.trim();
    if (!trimmedAlias) {
      toast.error('Certificate alias cannot be empty');
      return;
    }
    if (!trimmedPem.includes('BEGIN CERTIFICATE')) {
      toast.error('Invalid X.509 PEM certificate');
      return;
    }
    if (draft.caCerts.some((c) => c.alias === trimmedAlias)) {
      toast.error('Certificate alias already exists');
      return;
    }
    setDraft({
      ...draft,
      caCerts: [
        ...draft.caCerts,
        {
          alias: trimmedAlias.slice(0, 64),
          cert_pem: trimmedPem,
          enabled: true,
        },
      ],
    });
    setNewCertAlias('');
    setNewCertPem('');
  };

  const handleRemoveCaCert = (alias: string) => {
    setDraft({
      ...draft,
      caCerts: draft.caCerts.filter((c) => c.alias !== alias),
    });
  };

  return (
    <MemberCard
      title="Device policies"
      icon={Shield}
      tone="text-sky-300"
      testId="guardian-device-policy-card"
      isLoading={isLoading}
      aside={dirty ? <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2.5 py-0.5 text-amber-200" data-testid="device-policy-unsaved">Unsaved changes</span> : undefined}
    >
      <div className="space-y-3 text-xs">
        {/* OS System Update Policy */}
        <div className="space-y-2.5 rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
          <div className="flex items-start gap-2.5">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-sky-400/20 bg-sky-400/10">
              <Cpu size={15} className="text-sky-300" />
            </span>
            <div>
              <p className="font-semibold text-white">System updates</p>
              <p className="text-[11px] leading-relaxed text-slate-400">Controls how Android OS system updates are installed on {name}&apos;s device.</p>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            <div>
              <label htmlFor="os-update-policy-select" className="mb-1 block text-slate-400">When updates install</label>
              <select
                id="os-update-policy-select"
                value={draft.policyType}
                onChange={(e) => setDraft({ ...draft, policyType: e.target.value as OsUpdatePolicyType })}
                className="min-h-11 w-full rounded-xl border border-white/10 bg-slate-900 px-3 text-xs text-white"
                data-testid="os-update-policy-select"
              >
                <option value="default">The phone decides (default)</option>
                <option value="automatic">Right away</option>
                <option value="windowed">Overnight, in a set window</option>
                <option value="postpone">Hold off for up to 30 days</option>
              </select>
            </div>
            {draft.policyType === 'windowed' && (
              <div className="flex items-center gap-2">
                <div className="flex-1">
                  <label htmlFor="os-update-window-start" className="mb-1 block text-slate-400">Window starts</label>
                  <input
                    id="os-update-window-start"
                    type="time"
                    value={draft.windowStart}
                    onChange={(e) => setDraft({ ...draft, windowStart: e.target.value })}
                    className="min-h-11 w-full rounded-xl border border-white/10 bg-black/20 px-2.5 text-xs text-white"
                    data-testid="os-update-window-start"
                  />
                </div>
                <div className="flex-1">
                  <label htmlFor="os-update-window-end" className="mb-1 block text-slate-400">Window ends</label>
                  <input
                    id="os-update-window-end"
                    type="time"
                    value={draft.windowEnd}
                    onChange={(e) => setDraft({ ...draft, windowEnd: e.target.value })}
                    className="min-h-11 w-full rounded-xl border border-white/10 bg-black/20 px-2.5 text-xs text-white"
                    data-testid="os-update-window-end"
                  />
                </div>
              </div>
            )}
          </div>
        </div>

        {/* VPN Lockdown */}
        <div className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
          <div className="min-w-0 space-y-0.5">
            <p className="font-semibold text-white">Always-on VPN lockdown</p>
            <p className="text-[11px] leading-relaxed text-slate-400">
              No internet on {name}&apos;s phone unless the Jarvis web filter is connected.
            </p>
          </div>
          <Toggle
            checked={draft.vpnLockdown}
            onChange={(next) => setDraft({ ...draft, vpnLockdown: next })}
            ariaLabel="Always-on VPN lockdown"
            testId="toggle-vpn-lockdown"
          />
        </div>

        {/* Wi-Fi Provisioning */}
        <div className="space-y-2.5 rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
          <button
            type="button"
            onClick={() => setOpenPart(openPart === 'wifi' ? null : 'wifi')}
            aria-expanded={openPart === 'wifi'}
            className="flex min-h-11 w-full items-start gap-2.5 text-left"
            data-testid="device-policy-wifi-toggle"
          >
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-emerald-400/20 bg-emerald-400/10">
              <Wifi size={15} className="text-emerald-300" aria-hidden />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-semibold text-white">Wi-Fi networks</span>
              <span className="block text-[11px] leading-relaxed text-slate-400">
                {draft.wifiNetworks.length ? `${draft.wifiNetworks.length} set up on ${name}'s phone` : `Family Wi-Fi set up on ${name}'s phone`}
              </span>
            </span>
            <ChevronDown size={16} aria-hidden className={`mt-2 shrink-0 text-slate-400 transition-transform ${openPart === 'wifi' ? 'rotate-180' : ''}`} />
          </button>
          {openPart === 'wifi' && (<>
          {draft.wifiNetworks.length > 0 && (
            <ul className="divide-y divide-white/5 rounded-xl border border-white/5 bg-black/20 px-3" data-testid="wifi-network-list">
              {draft.wifiNetworks.map((net) => (
                <li key={net.ssid} className="flex items-center justify-between gap-2 py-1.5">
                  <div className="min-w-0">
                    <span className="font-medium text-white">{net.ssid}</span>
                    <span className="ml-2 text-[10px] text-slate-400">({net.security || 'wpa2'}{net.hidden ? ', hidden' : ''})</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleRemoveWifi(net.ssid)}
                    className="-mr-1 flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-xl text-slate-400 transition-colors hover:bg-white/10 hover:text-red-400"
                    title="Remove network"
                    aria-label={`Remove ${net.ssid}`}
                    data-testid={`remove-wifi-${net.ssid}`}
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="grid grid-cols-1 gap-2 pt-1 sm:grid-cols-2">
            <div>
              <label htmlFor="new-wifi-ssid" className="sr-only">SSID</label>
              <input
                id="new-wifi-ssid"
                type="text"
                placeholder="Network name (SSID)"
                autoComplete="off"
                value={newSsid}
                onChange={(e) => setNewSsid(e.target.value)}
                className="min-h-11 w-full rounded-xl border border-white/10 bg-black/20 px-3 text-xs text-white"
                data-testid="input-wifi-ssid"
              />
            </div>
            {newSecurity !== 'open' && <div>
              <label htmlFor="new-wifi-password" className="sr-only">Password</label>
              <input
                id="new-wifi-password"
                type="password"
                placeholder="Password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="min-h-11 w-full rounded-xl border border-white/10 bg-black/20 px-3 text-xs text-white"
                autoComplete="new-password"
                data-testid="input-wifi-password"
              />
            </div>}
            <div className="flex gap-2 sm:col-span-2">
              <label htmlFor="new-wifi-security" className="sr-only">Security</label>
              <select
                id="new-wifi-security"
                value={newSecurity}
                onChange={(e) => setNewSecurity(e.target.value as 'open' | 'wpa2' | 'wpa3' | 'wep')}
                className="min-h-11 flex-1 rounded-xl border border-white/10 bg-slate-900 px-3 text-xs text-white"
                data-testid="select-wifi-security"
              >
                <option value="wpa2">WPA2</option>
                <option value="wpa3">WPA3</option>
                <option value="open">Open</option>
                <option value="wep">WEP</option>
              </select>
              <button
                type="button"
                onClick={handleAddWifi}
                className="glass-button flex min-h-11 shrink-0 items-center justify-center gap-1.5 rounded-xl px-4 text-xs font-semibold text-white"
                title="Add network"
                data-testid="btn-add-wifi"
              >
                <Plus size={14} aria-hidden /> Add network
              </button>
            </div>
            <label className="flex min-h-11 items-center gap-2 text-slate-300 sm:col-span-2">
              <input
                type="checkbox"
                checked={newHidden}
                onChange={(e) => setNewHidden(e.target.checked)}
                className="h-4 w-4 accent-sky-400"
                data-testid="input-wifi-hidden"
              />
              Hidden network (it does not broadcast its name)
            </label>
          </div>
          </>)}
        </div>

        {/* CA Root Certificates */}
        <div className="space-y-2.5 rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
          <button
            type="button"
            onClick={() => setOpenPart(openPart === 'certs' ? null : 'certs')}
            aria-expanded={openPart === 'certs'}
            className="flex min-h-11 w-full items-start gap-2.5 text-left"
            data-testid="device-policy-certs-toggle"
          >
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-amber-400/20 bg-amber-400/10">
              <Shield size={15} className="text-amber-300" aria-hidden />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-semibold text-white">Trusted certificates</span>
              <span className="block text-[11px] leading-relaxed text-slate-400">
                {draft.caCerts.length ? `${draft.caCerts.length} installed` : 'Advanced: root certificates the phone trusts'}
              </span>
            </span>
            <ChevronDown size={16} aria-hidden className={`mt-2 shrink-0 text-slate-400 transition-transform ${openPart === 'certs' ? 'rotate-180' : ''}`} />
          </button>
          {openPart === 'certs' && (<>
          {draft.caCerts.length > 0 && (
            <ul className="divide-y divide-white/5 rounded-xl border border-white/5 bg-black/20 px-3" data-testid="ca-cert-list">
              {draft.caCerts.map((cert) => (
                <li key={cert.alias} className="flex items-center justify-between gap-2 py-1.5">
                  <span className="truncate font-medium text-white">{cert.alias}</span>
                  <button
                    type="button"
                    onClick={() => handleRemoveCaCert(cert.alias)}
                    className="-mr-1 flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-xl text-slate-400 transition-colors hover:bg-white/10 hover:text-red-400"
                    title="Remove certificate"
                    aria-label={`Remove ${cert.alias}`}
                    data-testid={`remove-cert-${cert.alias}`}
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="space-y-2 pt-1">
            <div>
              <label htmlFor="new-cert-alias" className="sr-only">Certificate Alias</label>
              <input
                id="new-cert-alias"
                type="text"
                placeholder="Name (e.g. JarvisHomeCA)"
                value={newCertAlias}
                onChange={(e) => setNewCertAlias(e.target.value)}
                className="min-h-11 w-full rounded-xl border border-white/10 bg-black/20 px-3 text-xs text-white"
                data-testid="input-cert-alias"
              />
            </div>
            <div>
              <label htmlFor="new-cert-pem" className="sr-only">Certificate PEM</label>
              <textarea
                id="new-cert-pem"
                rows={2}
                placeholder="-----BEGIN CERTIFICATE----- ... -----END CERTIFICATE-----"
                value={newCertPem}
                onChange={(e) => setNewCertPem(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-[11px] text-white"
                data-testid="input-cert-pem"
              />
            </div>
            <button
              type="button"
              onClick={handleAddCaCert}
              className="glass-button inline-flex min-h-11 items-center gap-1.5 rounded-xl px-3.5 text-xs font-semibold text-white"
              data-testid="btn-add-cert"
            >
              <Plus size={14} aria-hidden /> Add certificate
            </button>
          </div>
          </>)}
        </div>

        {/* Save button */}
        <div className="flex items-center justify-end gap-2 pt-1">
          {dirty && (
            <button
              type="button"
              onClick={() => setDraft(null)}
              disabled={saveMutation.isPending}
              className="min-h-11 rounded-xl px-4 text-xs font-medium text-slate-400 hover:text-white"
              data-testid="discard-device-policies"
            >
              Discard
            </button>
          )}
          <button
            type="button"
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending || !dirty}
            className="glass-button min-h-11 rounded-xl border border-sky-400/40 bg-sky-500/20 px-5 text-xs font-semibold text-sky-100 hover:bg-sky-500/30 disabled:opacity-50"
            data-testid="save-device-policies"
          >
            {saveMutation.isPending ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
          </button>
        </div>
      </div>
    </MemberCard>
  );
}
