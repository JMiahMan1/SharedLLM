export type WidgetKey =
  | 'energy_insights'
  | 'ambient_timer'
  | 'quick_notes'
  | 'active_media'
  | 'chores_progress'
  | 'upcoming_events'
  | 'quick_assistant'
  | 'device_control'
  | 'workspaces'
  | 'health_activity'
  | 'climate'
  | 'presence'
  | 'bible_daily';

export type WidgetVisibility = 'visible' | 'hidden' | 'removed';
export type WidgetSize = 'small' | 'medium' | 'wide' | 'tall';
export type DeviceSortMode = 'most_used' | 'by_time' | 'favorites' | 'off';

export interface UserWidgetSettings {
  widget_key: string;
  visibility: WidgetVisibility;
  order_index: number;
  size: WidgetSize;
  is_pinned: boolean;
  sort_mode: DeviceSortMode | null;
  pinned_devices: string[];
  config: Record<string, unknown>;
  updated_at: number;
}

export interface WidgetDef {
  key: WidgetKey;
  label: string;
  icon: React.ComponentType<{ size?: number }>;
  minSize: WidgetSize;
  defaultSize: WidgetSize;
  mountConditions?: (capabilities: CapabilityPayload) => boolean;
  requiresQuickAssistantEnabled?: boolean;
  /**
   * A management widget: hidden from normal users entirely, not merely
   * disabled. Resolved by `isWidgetVisible` in the widget store, which is the
   * one place both the dashboard and the settings catalog read from.
   */
  adminOnly?: boolean;
}

export interface CapabilityPayload {
  has_energy_data?: boolean;
  has_active_media?: boolean;
  has_chore_system?: boolean;
  has_skylight?: boolean;
  has_lights?: boolean;
  has_tvs?: boolean;
  has_timer?: boolean;
  has_notes?: boolean;
  has_events?: boolean;
  has_quick_assistant?: boolean;
  has_assignable_devices?: boolean;
}

export interface WidgetInstance {
  def: WidgetDef;
  userSettings: UserWidgetSettings;
  isActive: boolean;
}

export interface DeviceEntry {
  entity_id: string;
  friendly_name: string;
  domain: string;
  state: string;
  room?: string;
  last_activated?: number;
  attributes?: Record<string, number | string | boolean | string[] | null>;
}

export interface MediaState {
  entity_id: string;
  device_name: string;
  title: string;
  artist?: string;
  album?: string;
  thumbnail?: string;
  state: string;
  media_type?: string;
}

export interface CalendarEvent {
  id?: string;
  summary: string;
  start_time: string;
  end_time?: string;
  location?: string;
  integration?: string;
  calendar?: string;
}

/** A calendar owner (person). Events whose `calendar` is in `accounts` are
 *  colored/labeled by this person. Lets users assign accounts to people and
 *  merge duplicates (e.g. kalebsummers85 + kaleb). Stored in calendar settings. */
export interface CalendarPerson {
  id: string;
  name: string;
  color: string;
  accounts: string[];
}

export interface ChoreItem {
  id: string;
  title: string;
  completed: boolean;
  reward?: number;
  assignees?: string[];
  recurrence?: string;
  stars?: number;
  start?: string;
  start_time?: string | null;
  emoji_icon?: string | null;
}

export interface WidgetContextMenuProps {
  widgetKey: WidgetKey;
  userSettings: UserWidgetSettings;
  def: WidgetDef;
  onToggleVisibility: (widgetKey: WidgetKey, visible: boolean) => void;
  onTogglePin: (widgetKey: WidgetKey) => void;
  onResize: (widgetKey: WidgetKey, size: WidgetSize) => void;
  onReorder: (widgetKey: WidgetKey, newIndex: number) => void;
  /** Position of this widget within the currently visible board. */
  currentIndex: number;
  totalWidgets: number;
  onRemove: (widgetKey: WidgetKey) => void;
  className?: string;
}

export interface DeviceControlGroup {
  title: string;
  devices: DeviceEntry[];
}

export interface IWidgetProps {
  settingsButton: React.ReactNode;
  userSettings: UserWidgetSettings;
  onTogglePin: () => void;
}

export interface IActiveMediaWidgetProps extends IWidgetProps {
  onMediaStop?: () => void;
}

/**
 * Climate widget layout.
 * - `auto`  : one device renders as the dial, two or more render as tiles.
 * - `dial`  : always the Nest-style dial, focused on one device at a time.
 * - `tiles` : always the multi-room tile grid.
 */
export type ClimateLayout = 'auto' | 'dial' | 'tiles';

export interface ClimateConfig {
  /** climate.* entity ids shown by this widget, in display order. */
  devices?: string[];
  layout?: ClimateLayout;
}
