import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Pins the *call sites*, not the function.
 *
 * `deviceRegistration.test.ts` covers what registration does and would keep
 * passing if nothing ever called it. That is not hypothetical: registration
 * was only wired into the explicit login path, so anyone who installed an
 * update and kept using the app never registered, and no phone showed up in
 * the panel. A restored session is the common case, not the exception.
 */
const source = readFileSync(
  resolve(process.cwd(), 'src/context/AuthContext.tsx'),
  'utf8',
);

describe('device registration is wired into both ways of having a session', () => {
  it('registers on an explicit login', () => {
    // `logout` is declared *before* `login`, so anchor the slice from login
    // to the end of the file rather than to logout.
    const login = source.slice(source.indexOf('const login = useCallback'));
    expect(login).toMatch(/void registerThisDevice\(\)/);
  });

  it('registers when a stored session is restored', () => {
    const init = source.slice(
      source.indexOf('const initAuth = async'),
      source.indexOf('const logout'),
    );
    expect(init).toMatch(/void registerThisDevice\(\)/);
  });

  it('never awaits registration, so it cannot block or fail a session', () => {
    // Telemetry must not stand between a user and being logged in.
    expect(source).not.toMatch(/await registerThisDevice\(\)/);
    const occurrences = source.match(/void registerThisDevice\(\)/g) ?? [];
    expect(occurrences.length).toBeGreaterThanOrEqual(2);
  });
});

describe('the native location service gets credentials on a restored session too', () => {
  // The background location service reads the key, server and username from
  // TokenBridge. Only an explicit login used to fill it, so a phone signed in
  // from before the service existed tracked nothing while the app was closed.
  const init = source.slice(source.indexOf('const initAuth = async'), source.indexOf('const logout'));

  it('mirrors the stored credentials on restore', () => {
    expect(init).toMatch(/void mirrorNativeCredentials\(\)/);
  });

  it('mirrors the username on restore', () => {
    expect(init).toMatch(/void mirrorIdentity\(profile\.username\)/);
  });
});
