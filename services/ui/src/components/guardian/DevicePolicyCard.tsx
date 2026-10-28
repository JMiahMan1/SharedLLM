import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Cpu, Plus, Shield, Trash2, Wifi } from 'lucide-react';
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
      title="Device Policies & Security"
      icon={Shield}
      tone="text-sky-300"
      testId="guardian-device-policy-card"
      isLoading={isLoading}
    >
      <div className="space-y-4 text-xs">
        {/* OS System Update Policy */}
        <div className="space-y-2 rounded-xl border border-white/10 bg-white/5 p-3">
          <div className="flex items-center gap-2">
            <Cpu size={15} className="text-sky-400" />
            <div>
              <p className="font-semibold text-white">System Update Policy</p>
              <p className="text-[11px] text-slate-400">Controls how Android OS system updates are installed on {name}&apos;s device.</p>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div>
              <label htmlFor="os-update-policy-select" className="mb-1 block text-slate-400">Update Policy</label>
              <select
                id="os-update-policy-select"
                value={draft.policyType}
                onChange={(e) => setDraft({ ...draft, policyType: e.target.value as OsUpdatePolicyType })}
                className="w-full rounded-lg border border-white/10 bg-slate-900 px-2.5 py-1.5 text-xs text-white"
                data-testid="os-update-policy-select"
              >
                <option value="default">Default (System / User decision)</option>
                <option value="automatic">Automatic (Install immediately)</option>
                <option value="windowed">Maintenance Window (Install overnight)</option>
                <option value="postpone">Postpone (Delay up to 30 days)</option>
              </select>
            </div>
            {draft.policyType === 'windowed' && (
              <div className="flex items-center gap-2">
                <div className="flex-1">
                  <label htmlFor="os-update-window-start" className="mb-1 block text-slate-400">Window Start</label>
                  <input
                    id="os-update-window-start"
                    type="time"
                    value={draft.windowStart}
                    onChange={(e) => setDraft({ ...draft, windowStart: e.target.value })}
                    className="w-full rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-xs text-white"
                    data-testid="os-update-window-start"
                  />
                </div>
                <div className="flex-1">
                  <label htmlFor="os-update-window-end" className="mb-1 block text-slate-400">Window End</label>
                  <input
                    id="os-update-window-end"
                    type="time"
                    value={draft.windowEnd}
                    onChange={(e) => setDraft({ ...draft, windowEnd: e.target.value })}
                    className="w-full rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-xs text-white"
                    data-testid="os-update-window-end"
                  />
                </div>
              </div>
            )}
          </div>
        </div>

        {/* VPN Lockdown */}
        <div className="flex items-center justify-between rounded-xl border border-white/10 bg-white/5 p-3">
          <div className="space-y-0.5">
            <p className="font-semibold text-white">Always-on VPN Lockdown</p>
            <p className="text-[11px] text-slate-400">
              Blocks all device internet traffic if the Jarvis web filter VPN is disconnected.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setDraft({ ...draft, vpnLockdown: !draft.vpnLockdown })}
            data-testid="toggle-vpn-lockdown"
            className={`min-h-8 shrink-0 rounded-lg px-2.5 py-1 text-xs font-semibold transition-colors ${
              draft.vpnLockdown
                ? 'border border-sky-500/40 bg-sky-500/20 text-sky-300'
                : 'border border-white/10 bg-black/20 text-slate-400'
            }`}
          >
            {draft.vpnLockdown ? 'Lockdown On' : 'Off'}
          </button>
        </div>

        {/* Wi-Fi Provisioning */}
        <div className="space-y-2 rounded-xl border border-white/10 bg-white/5 p-3">
          <div className="flex items-center gap-2">
            <Wifi size={15} className="text-emerald-400" />
            <div>
              <p className="font-semibold text-white">Provisioned Wi-Fi Networks</p>
              <p className="text-[11px] text-slate-400">Automatically configures trusted family Wi-Fi networks on device.</p>
            </div>
          </div>
          {draft.wifiNetworks.length > 0 && (
            <ul className="divide-y divide-white/5" data-testid="wifi-network-list">
              {draft.wifiNetworks.map((net) => (
                <li key={net.ssid} className="flex items-center justify-between py-1.5">
                  <div>
                    <span className="font-medium text-white">{net.ssid}</span>
                    <span className="ml-2 text-[10px] text-slate-400">({net.security || 'wpa2'}{net.hidden ? ', hidden' : ''})</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleRemoveWifi(net.ssid)}
                    className="p-1 text-slate-400 hover:text-red-400"
                    title="Remove network"
                    data-testid={`remove-wifi-${net.ssid}`}
                  >
                    <Trash2 size={13} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="grid grid-cols-1 gap-2 pt-2 sm:grid-cols-4">
            <div className="sm:col-span-2">
              <label htmlFor="new-wifi-ssid" className="sr-only">SSID</label>
              <input
                id="new-wifi-ssid"
                type="text"
                placeholder="Wi-Fi SSID"
                value={newSsid}
                onChange={(e) => setNewSsid(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs text-white"
                data-testid="input-wifi-ssid"
              />
            </div>
            <div>
              <label htmlFor="new-wifi-password" className="sr-only">Password</label>
              <input
                id="new-wifi-password"
                type="password"
                placeholder="Password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs text-white"
                data-testid="input-wifi-password"
              />
            </div>
            <div className="flex gap-2">
              <label htmlFor="new-wifi-security" className="sr-only">Security</label>
              <select
                id="new-wifi-security"
                value={newSecurity}
                onChange={(e) => setNewSecurity(e.target.value as 'open' | 'wpa2' | 'wpa3' | 'wep')}
                className="flex-1 rounded-lg border border-white/10 bg-slate-900 px-2 py-1.5 text-xs text-white"
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
                className="glass-button flex items-center justify-center rounded-lg px-2.5 py-1 text-xs text-white"
                title="Add Wi-Fi"
                data-testid="btn-add-wifi"
              >
                <Plus size={14} />
              </button>
            </div>
          </div>
        </div>

        {/* CA Root Certificates */}
        <div className="space-y-2 rounded-xl border border-white/10 bg-white/5 p-3">
          <div className="flex items-center gap-2">
            <Shield size={15} className="text-amber-400" />
            <div>
              <p className="font-semibold text-white">Trusted CA Certificates</p>
              <p className="text-[11px] text-slate-400">Installs trusted root CA certificates into the device trust store.</p>
            </div>
          </div>
          {draft.caCerts.length > 0 && (
            <ul className="divide-y divide-white/5" data-testid="ca-cert-list">
              {draft.caCerts.map((cert) => (
                <li key={cert.alias} className="flex items-center justify-between py-1.5">
                  <span className="font-medium text-white">{cert.alias}</span>
                  <button
                    type="button"
                    onClick={() => handleRemoveCaCert(cert.alias)}
                    className="p-1 text-slate-400 hover:text-red-400"
                    title="Remove certificate"
                    data-testid={`remove-cert-${cert.alias}`}
                  >
                    <Trash2 size={13} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="space-y-2 pt-2">
            <div>
              <label htmlFor="new-cert-alias" className="sr-only">Certificate Alias</label>
              <input
                id="new-cert-alias"
                type="text"
                placeholder="Certificate Alias (e.g. JarvisHomeCA)"
                value={newCertAlias}
                onChange={(e) => setNewCertAlias(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs text-white"
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
                className="w-full rounded-lg border border-white/10 bg-black/20 px-2.5 py-1.5 font-mono text-[11px] text-white"
                data-testid="input-cert-pem"
              />
            </div>
            <button
              type="button"
              onClick={handleAddCaCert}
              className="glass-button flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs text-white"
              data-testid="btn-add-cert"
            >
              <Plus size={14} /> Add Certificate
            </button>
          </div>
        </div>

        {/* Save button */}
        <div className="flex justify-end pt-2">
          <button
            type="button"
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="glass-button min-h-9 rounded-xl bg-sky-500/20 px-4 py-2 font-medium text-sky-200 hover:bg-sky-500/30"
            data-testid="save-device-policies"
          >
            {saveMutation.isPending ? 'Saving…' : 'Save Policies'}
          </button>
        </div>
      </div>
    </MemberCard>
  );
}
