import DeviceSheet from '../DeviceSheet';
import { MoreControlsContext } from '../../dashboards/moreControls';
import DeviceControl from './registry';
import DeviceGlyph from '../DeviceGlyph';
import { accentFor, isActiveState } from '../../../lib/deviceIcons';
import type { HubEntity } from '../../../lib/dashboards';
import { stateLine, tileKind } from '../../../lib/deviceTiles';

/**
 * One entity's full control in a sheet, with its device's other entities (a TV's remote, a plug's
 * power reading): what the Rooms page opens when a name is tapped, like Home Assistant's
 * more-info dialog.
 */
export default function ControlSheet({ entity, siblings, area, onClose }: {
  entity: HubEntity; siblings: HubEntity[]; area?: string | null; onClose: () => void;
}) {
  const kind = tileKind({ kind: undefined, name: entity.name, model: null, manufacturer: null }, entity);
  const offline = entity.state === 'unavailable';
  const line = stateLine({ status: 'online', last_seen: null }, entity);
  return (
    <DeviceSheet title={entity.name} onClose={onClose} header={(
      <div className="flex min-w-0 items-center gap-3">
        <DeviceGlyph kind={kind} accent={accentFor(kind, entity)} active={!offline && isActiveState(entity)} size="lg" />
        <div className="min-w-0">
          <h2 className="line-clamp-2 break-words text-lg font-bold leading-tight text-white">{entity.name}</h2>
          <p className={`mt-0.5 truncate text-sm ${offline ? 'text-rose-200' : 'text-slate-400'}`}>
            {[area, line].filter(Boolean).join(' · ')}</p>
        </div>
      </div>
    )}>
      <MoreControlsContext.Provider value={null}>
        <DeviceControl entity={entity} siblings={siblings} disabled={offline} />
      </MoreControlsContext.Provider>
    </DeviceSheet>
  );
}
