import ClimateControl from './ClimateControl';
import LightControl from './LightControl';
import SwitchControl from './SwitchControl';
import VacuumControl from './VacuumControl';
import { SpeakerControl, TvControl } from './MediaControls';
import {
  CameraControl, CoverControl, FanControl, GarageControl, GenericControl, LockControl, SensorControl, SirenControl,
  UpdateControl,
} from './OtherControls';
import type { ControlProps } from './primitives';
import type { ComponentType } from 'react';
import type { ControlKind } from '../../../lib/deviceControls';

/**
 * The device control library's one registry: a control per kind, chosen from the entity alone
 * (lib/deviceControls.controlKind: domain, device_class, supported_features). A new device of a
 * known kind gets its control with no new code; a new kind is one entry here.
 *
 * Used by the Devices detail (HubDeviceSheet), the Rooms page's "more" sheet (ControlSheet) and
 * the dashboard's Devices widget.
 */
export const CONTROLS: Record<ControlKind, ComponentType<ControlProps>> = {
  light: LightControl,
  switch: SwitchControl,
  fan: FanControl,
  tv: TvControl,
  speaker: SpeakerControl,
  remote: GenericControl,
  vacuum: VacuumControl,
  camera: CameraControl,
  climate: ClimateControl,
  cover: CoverControl,
  garage: GarageControl,
  lock: LockControl,
  siren: SirenControl,
  button: GenericControl,
  number: GenericControl,
  select: GenericControl,
  time: GenericControl,
  update: UpdateControl,
  sensor: SensorControl,
  binary: SensorControl,
  image: GenericControl,
  scene: GenericControl,
  other: GenericControl,
};

