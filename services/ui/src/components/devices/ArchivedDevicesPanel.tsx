import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, ArchiveRestore, Save, Trash2 } from 'lucide-react';
import SetupSection from './SetupSection';
import { BTN, DANGER, INPUT } from './setupStyles';
import { confirmDialog } from '../ui/confirmDialog';
import Toggle from '../ui/Toggle';
import { failureText } from '../../lib/discoveredDevices';
import { ago } from '../../lib/hardware';
import {
  deleteArchived, deletionText, getHubArchive, getIdentityArchive, mergeArchived, restoreArchived,
  saveArchiveSettings, settingsProblem, shortDate, sourceText, type ArchiveSettings, type ArchivedItem,
} from '../../lib/deviceArchive';

const HUB_KEY = ['homehub-archive'];
const IDENTITY_KEY = ['identity-device-archive'];

/*
 * Devices > Setup > Archived devices (admins). A device that has not been heard from for the set number of days
 * is archived: not retried, not offered to voice or listed, but its settings, room, name and history are kept.
 * It comes back by itself when it shows up on the network again, or with Restore here. A year after archiving
 * (the second setting) it is deleted for good, unless it came back. Delete now does that at once.
 */

function SettingsForm({ settings, onSaved }: { settings: ArchiveSettings; onSaved: () => void }) {
  const [archiveDays, setArchiveDays] = useState(String(settings.archive_after_days));
  const [deleteDays, setDeleteDays] = useState(String(settings.delete_after_days));
  const [enabled, setEnabled] = useState(settings.enabled);
  const values = { archive_after_days: Number(archiveDays), delete_after_days: Number(deleteDays) };
  const problem = settingsProblem(values);
  const save = useMutation({ mutationFn: () => saveArchiveSettings({ ...values, enabled }), onSuccess: onSaved });
  const changed = enabled !== settings.enabled || values.archive_after_days !== settings.archive_after_days
    || values.delete_after_days !== settings.delete_after_days;
  return (
    <form className="space-y-3 border-b border-white/5 px-4 py-3" data-testid="archive-settings"
      onSubmit={(e) => { e.preventDefault(); if (!problem) save.mutate(); }}>
      <div className="flex min-h-11 items-center gap-3 text-sm text-slate-200">
        <Toggle checked={enabled} onChange={setEnabled} ariaLabel="Archive devices that stop answering" />
        <span>Archive devices that stop answering</span>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="min-w-0 text-xs text-slate-400">
          Archive after (days without contact)
          <input className={`${INPUT} mt-1`} inputMode="numeric" value={archiveDays} disabled={!enabled}
            onChange={(e) => setArchiveDays(e.target.value.replace(/[^0-9]/g, ''))} aria-label="Archive after days" />
        </label>
        <label className="min-w-0 text-xs text-slate-400">
          Delete after (days archived)
          <input className={`${INPUT} mt-1`} inputMode="numeric" value={deleteDays} disabled={!enabled}
            onChange={(e) => setDeleteDays(e.target.value.replace(/[^0-9]/g, ''))} aria-label="Delete after days" />
        </label>
      </div>
      {problem && changed && <p className="text-xs text-amber-200/90" role="alert">{problem}</p>}
      {save.isError && <p className="text-xs text-rose-300" role="alert">{failureText(save.error)}</p>}
      <button type="submit" className={BTN} disabled={!changed || Boolean(problem) || save.isPending}>
        <Save size={14} aria-hidden /> Save
      </button>
    </form>
  );
}

function Row({ item, busy, onRestore, onDelete }: {
  item: ArchivedItem; busy: boolean; onRestore: () => void; onDelete: () => void;
}) {
  const source = sourceText(item);
  return (
    <li className="space-y-2 px-4 py-3" data-testid="archived-device">
      <div className="min-w-0">
        <p className="break-words text-sm font-medium text-white">{item.name}</p>
        {source && <p className="break-words text-xs text-slate-400">{source}</p>}
      </div>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        <div className="min-w-0">
          <dt className="text-slate-500">Last seen</dt>
          <dd className="text-slate-200" data-testid="archived-last-seen">
            {item.last_seen ? `${shortDate(item.last_seen)} (${ago(item.last_seen)})` : 'Never'}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-slate-500">Archived since</dt>
          <dd className="text-slate-200" data-testid="archived-since">{shortDate(item.archived_at)}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-slate-500">Deletion</dt>
          <dd className="text-slate-200" data-testid="archived-deletion">{deletionText(item)}</dd>
        </div>
      </dl>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BTN} disabled={busy} onClick={onRestore} aria-label={`Restore ${item.name}`}>
          <ArchiveRestore size={14} aria-hidden /> Restore
        </button>
        <button type="button" className={DANGER} disabled={busy} onClick={onDelete} aria-label={`Delete ${item.name} now`}>
          <Trash2 size={14} aria-hidden /> Delete now
        </button>
      </div>
    </li>
  );
}

export default function ArchivedDevicesPanel() {
  const qc = useQueryClient();
  const hub = useQuery({ queryKey: HUB_KEY, queryFn: getHubArchive, retry: false });
  const identity = useQuery({ queryKey: IDENTITY_KEY, queryFn: getIdentityArchive, retry: false });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: HUB_KEY });
    void qc.invalidateQueries({ queryKey: IDENTITY_KEY });
    void qc.invalidateQueries({ queryKey: ['homehub-hardware'] });
    void qc.invalidateQueries({ queryKey: ['mdm-devices'] });
  };
  const restore = useMutation({ mutationFn: restoreArchived, onSettled: refresh });
  const remove = useMutation({ mutationFn: deleteArchived, onSettled: refresh });
  const items = mergeArchived(hub.data?.items, identity.data?.items);
  const settings = hub.data?.settings ?? identity.data?.settings;
  const busy = restore.isPending || remove.isPending;
  const error = restore.error ?? remove.error;

  const askDelete = async (item: ArchivedItem) => {
    if (await confirmDialog({
      title: `Delete ${item.name}?`,
      body: 'Its settings, history and stored logins are removed for good. If it shows up again it has to be set up '
        + 'like a new device.',
      confirmLabel: 'Delete', danger: true,
    })) remove.mutate(item);
  };

  return (
    <SetupSection title="Archived devices" icon={Archive} testId="archived-devices"
      aside={items.length ? <span className="text-xs text-slate-400">{items.length} archived</span> : undefined}>
      <p className="px-4 pt-3 text-xs text-slate-400">
        Devices that have not answered for a long time. They are not retried, and voice and the device lists leave
        them out, but their settings, room and history are kept. One that shows up again comes back by itself.
      </p>
      {settings && (
        // Keyed by the saved values, so the form starts again from what the server holds after a save.
        <SettingsForm key={`${settings.enabled}:${settings.archive_after_days}:${settings.delete_after_days}`}
          settings={settings} onSaved={refresh} />
      )}
      {hub.isLoading && identity.isLoading && <p className="px-4 py-3 text-sm text-slate-400">Loading…</p>}
      {hub.isError && <p className="px-4 py-2 text-xs text-rose-300" role="alert">The device hub did not answer.</p>}
      {identity.isError && (
        <p className="px-4 py-2 text-xs text-rose-300" role="alert">Jarvis's registered devices could not be read.</p>
      )}
      {error && <p className="px-4 py-2 text-xs text-rose-300" role="alert">{failureText(error)}</p>}
      {!hub.isLoading && !identity.isLoading && items.length === 0 && (
        <p className="px-4 py-3 text-sm text-slate-400" data-testid="archived-empty">No archived devices.</p>
      )}
      {items.length > 0 && (
        <ul className="divide-y divide-white/5">
          {items.map((item) => (
            <Row key={`${item.kind}:${item.id}`} item={item} busy={busy}
              onRestore={() => restore.mutate(item)} onDelete={() => void askDelete(item)} />
          ))}
        </ul>
      )}
    </SetupSection>
  );
}
