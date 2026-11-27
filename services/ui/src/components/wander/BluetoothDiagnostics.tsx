import { useCallback, useEffect, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { Stethoscope, RefreshCw } from 'lucide-react';
import VehicleBluetooth, { type VehicleBtDiagnostics } from '../../plugins/vehicleBluetooth';
import Proximity, { type ProximityDiagnostics } from '../../plugins/proximity';
import { bandLabel } from '../../lib/proximity';
import FamilyMesh, { type MeshDiagnostics } from '../../plugins/familyMesh';
import OfflineMode, { type OfflineDiagnostics } from '../../plugins/offlineMode';
import OwnSync, { type OwnSyncDiagnostics, type OwnSyncState } from '../../plugins/ownSync';
import { OWN_SYNC_CATEGORIES, peerName, statusText } from '../../lib/ownSync';

/** One line of the panel: what is checked, and what this phone says. */
export function DiagRow({ label, value, ok, testId }: { label: string; value: string; ok?: boolean | null; testId?: string }) {
  const tone = ok === true ? 'text-emerald-300' : ok === false ? 'text-amber-300' : 'text-slate-200';
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-1" data-testid={testId}>
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className={`min-w-0 break-words text-xs font-medium ${tone}`}>{value}</dd>
    </div>
  );
}

function when(ms: number | null | undefined): string {
  if (!ms) return 'never';
  return new Date(ms).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/**
 * This phone's Bluetooth diagnostics (Admin > Users & Devices), for a parent
 * checking a real phone (the Android app only): enough to verify each step of the manual checklist
 * without a cable. Vehicle detection: the Nearby devices permission,
 * Bluetooth, the linked car connected now (what the next fix carries).
 * Family nearby: the advertiser (on, or unsupported on a scan-only tablet),
 * the scan, and the phones heard with their band and signal. Offline chat:
 * the last message sent and received, and the last phone-to-phone sync.
 * My devices: for each of this person's other devices, when it last synced and,
 * per kind of data, "In sync" or "Updated from <device> at <time>".
 */
export default function BluetoothDiagnostics() {
  const android = Capacitor.getPlatform() === 'android';
  const [vehicle, setVehicle] = useState<VehicleBtDiagnostics | null>(null);
  const [nearby, setNearby] = useState<ProximityDiagnostics | null>(null);
  const [mesh, setMesh] = useState<MeshDiagnostics | null>(null);
  const [offlineDiag, setOfflineDiag] = useState<OfflineDiagnostics | null>(null);
  const [own, setOwn] = useState<OwnSyncDiagnostics | null>(null);
  const [ownState, setOwnState] = useState<OwnSyncState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([
      VehicleBluetooth.getDiagnostics(),
      Proximity.getDiagnostics().catch(() => null),
      FamilyMesh.getDiagnostics().catch(() => null),
      OfflineMode.getDiagnostics().catch(() => null),
      // An app from before My devices has no such plugin: the section is left out.
      OwnSync.getDiagnostics().catch(() => null),
      OwnSync.state().catch(() => null),
    ])
      .then(([v, n, m, o, od, os]) => {
        setVehicle(v);
        setNearby(n);
        setMesh(m);
        setOfflineDiag(o);
        setOwn(od);
        setOwnState(os);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Could not read this phone'));
  }, []);

  useEffect(() => {
    if (android) load();
  }, [android, load]);

  if (!android) return null;

  return (
    <details className="glass-panel rounded-2xl border border-white/5 p-4" data-testid="bt-diagnostics">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 text-sm font-semibold text-slate-200">
        <Stethoscope size={15} className="text-sky-300" /> Bluetooth diagnostics (this phone)
      </summary>
      <div className="mt-2 space-y-3">
        {error && <p className="text-xs text-amber-300">{error}</p>}
        {vehicle && (
          <section>
            <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Vehicle detection</h4>
            <dl className="divide-y divide-white/5">
              <DiagRow label="Android API" value={String(vehicle.apiLevel)} />
              <DiagRow label="Nearby devices (connect)" value={vehicle.permissionGranted ? 'Allowed' : 'Not allowed'} ok={vehicle.permissionGranted} testId="diag-bt-permission" />
              <DiagRow label="Bluetooth" value={vehicle.supported ? (vehicle.bluetoothOn ? 'On' : 'Off') : 'Not on this device'} ok={vehicle.supported && vehicle.bluetoothOn} />
              <DiagRow
                label="Linked car connected now"
                value={vehicle.linkedConnected.length
                  ? vehicle.linkedConnected.map((v) => `${v.vehicle ?? 'Vehicle'} (${v.address})`).join(', ')
                  : 'None'}
                ok={vehicle.linkedConnected.length > 0 ? true : null}
                testId="diag-bt-vehicle"
              />
              <DiagRow label="Other devices connected" value={String(vehicle.otherConnectedCount)} />
              <DiagRow label="Household links known" value={`${vehicle.linkedCount}, read ${when(vehicle.linksRefreshedAt)}`} />
            </dl>
          </section>
        )}
        {nearby && (
          <section>
            <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Family nearby</h4>
            <dl className="divide-y divide-white/5">
              <DiagRow label="Turned on" value={nearby.enabled ? 'Yes' : 'No'} ok={nearby.enabled} />
              <DiagRow label="Nearby devices (scan)" value={nearby.scanPermission ? 'Allowed' : 'Not allowed'} ok={nearby.scanPermission} />
              <DiagRow
                label="Advertiser"
                value={nearby.advertising === 'unsupported' ? 'Not supported on this device (scan only)'
                  : nearby.advertising === 'failed' ? `Failed${nearby.advertiseError ? ` (error ${nearby.advertiseError})` : ''}`
                  : nearby.advertising === 'on' ? 'On' : 'Off'}
                ok={nearby.advertising === 'on' ? true : nearby.advertising === 'unsupported' ? null : false}
                testId="diag-advertiser"
              />
              <DiagRow
                label="Scan"
                value={nearby.scanning ? `Running (${nearby.scanMode === 'balanced' ? 'trip' : 'low power'})`
                  : nearby.running ? `Paused between bursts, last ${when(nearby.lastScanStartAt)}` : 'Not running'}
                ok={nearby.running}
                testId="diag-scan"
              />
              <DiagRow label="Family key" value={nearby.configured ? `${nearby.keyId} (${nearby.members} members)` : 'Not received yet'} ok={nearby.configured} />
              <DiagRow
                label="Phones heard"
                value={nearby.peers.length
                  ? nearby.peers.map((p) => `${p.ownDevice ? 'Your other device' : p.person}: ${bandLabel(p.band)} (${p.rssi} dBm, ${when(p.at)})`).join('; ')
                  : 'None yet'}
                testId="diag-peers"
              />
              <DiagRow label="Sightings waiting to send" value={`${nearby.outbox}, last sent ${when(nearby.uploadedAt)}`} />
            </dl>
          </section>
        )}
        {mesh && (
          <section>
            <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Offline chat</h4>
            <dl className="divide-y divide-white/5">
              <DiagRow label="Turned on" value={mesh.enabled ? 'Yes' : 'No'} ok={mesh.enabled} />
              <DiagRow label="Nearby devices (connect)" value={mesh.connectPermission ? 'Allowed' : 'Not allowed'} ok={mesh.connectPermission} />
              <DiagRow label="Chats ready offline" value={String(mesh.rooms ?? 0)} ok={(mesh.rooms ?? 0) > 0} testId="diag-mesh-rooms" />
              <DiagRow label="Accepts connections" value={mesh.server ? 'Yes' : 'No (connects out only)'} ok={mesh.server ? true : null} />
              <DiagRow label="Last offline message sent" value={when(mesh.lastSentAt)} testId="diag-mesh-sent" />
              <DiagRow label="Last offline message received" value={when(mesh.lastReceivedAt)} testId="diag-mesh-received" />
              <DiagRow
                label="Last phone-to-phone sync"
                value={mesh.lastPeerSync ? `${mesh.lastPeerSync.peer || 'a phone'} at ${when(mesh.lastPeerSync.at)}: sent ${mesh.lastPeerSync.sent}, got ${mesh.lastPeerSync.received}` : 'never'}
                testId="diag-mesh-peer"
              />
              <DiagRow label="Held on this phone" value={`${mesh.stored} (${mesh.unsynced} of mine not yet in their chat)`} />
              <DiagRow label="Last sent to the chats" value={when(mesh.lastServerSyncAt)} />
              {mesh.lastError && <DiagRow label="Last problem" value={mesh.lastError} ok={false} />}
            </dl>
          </section>
        )}
        {offlineDiag && (
          <section>
            <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Offline mode</h4>
            <dl className="divide-y divide-white/5">
              <DiagRow label="Server reachable" value={offlineDiag.online ? 'Yes' : 'No'} ok={offlineDiag.online} />
              <DiagRow label="Family phone heard (5 min)" value={offlineDiag.peerNearby ? 'Yes' : 'No'} ok={offlineDiag.peerNearby ? true : null} />
              <DiagRow label="My Jarvis requests waiting" value={String(offlineDiag.queued)} testId="diag-offline-queued" />
              <DiagRow label="Carrying for others" value={String(offlineDiag.carrying)} />
              <DiagRow label="Last relayed to Jarvis" value={when(offlineDiag.lastRelayAt)} testId="diag-offline-relay" />
              <DiagRow label="Household information" value={`${offlineDiag.cacheItems} items, refreshed ${when(offlineDiag.snapshotAt)}`} />
              <DiagRow label="Parent commands waiting" value={String(offlineDiag.commandsWaiting)} />
              {offlineDiag.protectedPhone && (
                <DiagRow label="Parent keys trusted" value={`${offlineDiag.parentKeys} (version ${offlineDiag.keysVersion || 'none'})`}
                  ok={offlineDiag.parentKeys > 0} testId="diag-offline-parent-keys" />
              )}
            </dl>
          </section>
        )}
        {own && (
          <section data-testid="diag-own">
            <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">My devices</h4>
            <dl className="divide-y divide-white/5">
              <DiagRow label="Turned on" value={own.turnedOn ? (own.enabled ? 'Yes' : 'Yes, waiting for Nearby devices') : 'No'}
                ok={own.enabled} testId="diag-own-on" />
              {ownState?.steps && (
                <DiagRow
                  label="Steps today, all my devices"
                  value={`${ownState.steps.total.toLocaleString('en-US')} (${ownState.steps.devices
                    .map((d) => `${d.me ? 'this one' : d.name || 'another device'} ${d.steps.toLocaleString('en-US')}`).join(', ')})`}
                  testId="diag-own-steps"
                />
              )}
              {ownState?.latestFix && (
                <DiagRow
                  label="Latest location"
                  value={`From ${ownState.latestFix.device === ownState.deviceId ? 'this one' : ownState.latestFix.name || 'another device'}, ${when(ownState.latestFix.at)}`}
                  testId="diag-own-fix"
                />
              )}
              {own.peers.length === 0 && (
                <DiagRow label="My other devices" value="None synced yet" testId="diag-own-none" />
              )}
            </dl>
            {own.peers.map((peer) => (
              <div key={peer.device} className="mt-2 rounded-xl bg-white/5 px-3 py-1" data-testid="diag-own-peer">
                <p className="break-words pt-1 text-xs font-semibold text-slate-200">{peerName(peer)}</p>
                <dl className="divide-y divide-white/5">
                  <DiagRow label="Last synced" value={when(peer.last_sync_at)} testId="diag-own-peer-synced" />
                  {OWN_SYNC_CATEGORIES.map((c) => (
                    <DiagRow
                      key={c.id}
                      label={c.label}
                      value={statusText(peer.types[c.id])}
                      ok={peer.types[c.id] ? true : null}
                      testId={`diag-own-type-${c.id}`}
                    />
                  ))}
                </dl>
              </div>
            ))}
          </section>
        )}
        <button type="button" onClick={load} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-3 text-xs font-semibold text-purple-300 hover:bg-white/10">
          <RefreshCw size={13} /> Refresh
        </button>
      </div>
    </details>
  );
}
