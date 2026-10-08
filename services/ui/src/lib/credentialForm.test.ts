import { describe, expect, it } from 'vitest';
import { INTEGRATIONS, credentialPayload, missingFields, validateIntegrations } from './credentialForm';

const nextcloud = INTEGRATIONS.find((i) => i.id === 'nextcloud')!;
const abs = INTEGRATIONS.find((i) => i.id === 'audiobookshelf')!;
const none = new Set<string>();

describe('credential form rules', () => {
  it('never sends a blank field, so a blank can never erase a saved login', () => {
    const body = credentialPayload({ nextcloud_url: 'https://c', nextcloud_pass: '', ha_token: '   ' }, ['nextcloud_url', 'nextcloud_pass', 'ha_token']);
    expect(body).toEqual({ nextcloud_url: 'https://c' });
  });

  it('asks for removal by name', () => {
    expect(credentialPayload({}, ['ha_url'], ['ha_url', 'ha_token'])).toEqual({ clear_fields: ['ha_url', 'ha_token'] });
  });

  it('will not save half a login', () => {
    expect(missingFields(nextcloud, { nextcloud_url: 'https://c' }, none)).toEqual(['Nextcloud Username', 'Nextcloud Password']);
  });

  it('counts a saved secret as present', () => {
    const values = { nextcloud_url: 'https://c', nextcloud_user: 'michele', nextcloud_pass: '' };
    expect(missingFields(nextcloud, values, new Set(['nextcloud_pass']))).toEqual([]);
  });

  it('leaves an untouched integration alone', () => {
    expect(validateIntegrations({}, none)).toEqual([]);
  });

  it('accepts either way of completing Audiobookshelf', () => {
    expect(missingFields(abs, { audiobookshelf_url: 'u', audiobookshelf_api_key: 'k' }, none)).toEqual([]);
    expect(missingFields(abs, { audiobookshelf_url: 'u', audiobookshelf_user: 'a', audiobookshelf_pass: 'p' }, none)).toEqual([]);
    expect(missingFields(abs, { audiobookshelf_url: 'u' }, none)).toEqual(['Audiobookshelf API Key']);
  });
});
