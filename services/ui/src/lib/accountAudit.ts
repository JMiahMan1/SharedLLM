// Plain-language rendering of account audit events (see AccountAuditPanel).
import type { AccountAuditEvent } from '../types/api';

const ACTION_LABELS: Record<string, string> = {
  'user.update': 'edited',
  'user.self_update': 'updated their own account',
  'user.create': 'created',
  'user.delete': 'deleted',
  'user.import': 'imported',
  'user.password_reset': 'reset the password of',
  'credential.reveal': 'viewed a saved credential of',
  'credential.share': 'changed shared-login access for',
  'credential.seed': 'seeded a system credential on',
  'credential.service_token': 'stored a service token for',
  'api_key.create': 'created an API key for',
  'api_key.revoke': 'revoked an API key for',
};

const FIELD_LABELS: Record<string, string> = {
  nextcloud_pass: 'Nextcloud password',
  nextcloud_user: 'Nextcloud username',
  nextcloud_url: 'Nextcloud URL',
  ha_token: 'Home Assistant token',
  ha_url: 'Home Assistant URL',
  mass_token: 'Music Assistant token',
  mass_url: 'Music Assistant URL',
  audiobookshelf_pass: 'Audiobookshelf password',
  audiobookshelf_api_key: 'Audiobookshelf API key',
  display_name: 'display name',
  is_admin: 'admin',
  password: 'sign-in password',
};

/** "jeremiah edited michele" -- the sentence a person would say. */
export function describeEvent(event: AccountAuditEvent): string {
  const verb = ACTION_LABELS[event.action] ?? event.action;
  const self = event.action === 'user.self_update' || event.actor === event.target;
  const target = event.target && !(event.action === 'user.self_update') ? ` ${self ? 'their own account' : event.target}` : '';
  return `${event.actor}${event.actor_kind === 'internal' ? ' (system)' : ''} ${verb}${target}`;
}

export function describeChange(change: AccountAuditEvent['changes'][number]): string {
  const name = FIELD_LABELS[change.field] ?? change.field.replace(/_/g, ' ');
  if (change.to !== undefined && typeof change.to === 'boolean') return `${name} → ${change.to ? 'on' : 'off'}`;
  if (Array.isArray(change.to)) return `${name} → ${change.to.length ? change.to.join(', ') : 'none'}`;
  return `${name} ${change.change}`;
}
