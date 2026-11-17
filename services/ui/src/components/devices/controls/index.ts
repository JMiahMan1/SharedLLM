/** The device control library: one control per kind, chosen by `DeviceControl` from the entity. */
export { default as DeviceControl } from './registry';
export { CONTROLS } from './controlMap';
export { default as ControlSheet } from './ControlSheet';
export { default as BigSlider } from './BigSlider';
export { PowerRow, OptionPicker, History, NotAvailable, type ControlProps } from './primitives';
export { controlKind, type ControlKind } from '../../../lib/deviceControls';
