import { CONTROLS } from './controlMap';
import type { HubEntity } from '../../../lib/dashboards';
import { useEntityCommand, useServiceCall } from '../../../lib/deviceCommand';
import { controlKind } from '../../../lib/deviceControls';

/**
 * The full control for one entity, with its device's other entities beside it. Commands are drawn
 * at once and rolled back with the hub's own message if refused.
 */
export default function DeviceControl({ entity, name, disabled = false, siblings = [], onChanged }: {
  entity: HubEntity; name?: string; disabled?: boolean; siblings?: HubEntity[];
  /** Called after every command, for a caller that keeps its own copy of the state (the dashboard widget). */
  onChanged?: () => void;
}) {
  const label = name || entity.name;
  const { shown, send } = useEntityCommand(entity, label);
  const call = useServiceCall();
  const e = shown ?? entity;
  const Control = CONTROLS[controlKind(e)];
  return (
    <div data-testid="device-control" data-kind={controlKind(e)}>
      <Control e={e} name={label} disabled={disabled}
        send={async (service, data) => { const ok = await send(service, data); onChanged?.(); return ok; }}
        call={async (domain, service, id, data) => { const ok = await call(domain, service, id, data, label); onChanged?.(); return ok; }}
        siblings={siblings.filter((s) => s.entity_id !== entity.entity_id)} />
    </div>
  );
}
