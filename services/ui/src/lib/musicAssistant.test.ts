import { describe, expect, it } from 'vitest';
import { musicAssistantIsError, musicAssistantLine, offersHousehold, offersNewLogin, type MusicAssistantStatus } from './musicAssistant';

const status = (extra: Partial<MusicAssistantStatus>): MusicAssistantStatus => ({
  server: 'household', login: 'own', url: 'http://music.example.test:8095', own_url: null,
  household_url: 'http://music.example.test:8095', household_error: null, can_use_household: false,
  note: 'favourites are shared', ma_username: 'sam', state: 'ok', message: 'Connected as sam.', ...extra,
});

describe('musicAssistantLine', () => {
  it('names the server and whose login it is', () => {
    expect(musicAssistantLine(status({}))).toBe('Household server, your account');
    expect(musicAssistantLine(status({ login: 'provisioned' }))).toBe('Household server, your account');
    expect(musicAssistantLine(status({ login: 'granted' }))).toBe('Household server, shared login');
    expect(musicAssistantLine(status({ server: 'own', own_url: 'http://mine.test:8095' }))).toBe('Your own server');
  });

  it('says what is wrong, and whose server it is', () => {
    expect(musicAssistantLine(status({ server: 'own', state: 'unreachable' }))).toBe('Your server is not answering');
    expect(musicAssistantLine(status({ state: 'unreachable' }))).toBe('Not answering');
    expect(musicAssistantLine(status({ state: 'token_refused' }))).toBe('Token refused');
    expect(musicAssistantLine(status({ state: 'not_set_up', server: 'absent', url: null }))).toBe('Not set up');
    expect(musicAssistantLine(undefined, true)).toBe('Checking...');
    expect(musicAssistantLine(undefined)).toBe('Status unavailable');
  });
});

describe('the offers', () => {
  it('offers the household server only to someone with their own URL, when there is one', () => {
    expect(offersHousehold(status({ can_use_household: true, server: 'own', state: 'unreachable' }))).toBe(true);
    expect(offersHousehold(status({}))).toBe(false);
  });

  it('offers a new login only for a refused token on the household server, never for a shared one', () => {
    expect(offersNewLogin(status({ state: 'token_refused' }))).toBe(true);
    expect(offersNewLogin(status({ state: 'token_refused', login: 'granted' }))).toBe(false);
    expect(offersNewLogin(status({ state: 'token_refused', server: 'own' }))).toBe(false);
    expect(offersNewLogin(status({}))).toBe(false);
  });

  it('treats anything but a connected state as an error', () => {
    expect(musicAssistantIsError(status({}))).toBe(false);
    expect(musicAssistantIsError(status({ state: 'not_set_up' }))).toBe(true);
    expect(musicAssistantIsError(undefined)).toBe(false);
  });
});
