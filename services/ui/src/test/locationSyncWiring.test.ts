import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Pins the *wiring*, not the decision function.
 *
 * `locationSync.test.ts` covers the rule, and would keep passing even if
 * LocationContext stopped calling it -- which is exactly the bug being fixed
 * here: the rule existed nowhere, and the stationary branch simply returned.
 * So these assert the real upload path still uses the rule.
 */
const source = readFileSync(
  resolve(process.cwd(), 'src/context/LocationContext.tsx'),
  'utf8',
);

/** The body of the stationary-inside-geofence branch. */
function stationaryBranch(): string {
  const start = source.indexOf('if (insideGeofence && newInterval === \'stationary\')');
  expect(start).toBeGreaterThan(-1);
  const end = source.indexOf('lastLocationRef.current = {', start);
  return source.slice(start, end);
}

describe('LocationContext stationary upload', () => {
  it('still syncs steps while stationary', () => {
    expect(stationaryBranch()).toMatch(/void syncDailySteps\(\)/);
  });

  // The regression. The original branch was `void syncDailySteps(); return;`,
  // so a person sitting at home was never uploaded and their pin aged out.
  it('does not unconditionally return without uploading', () => {
    const branch = stationaryBranch();
    expect(branch).not.toMatch(/void syncDailySteps\(\);\s*\n\s*return;/);
  });

  it('consults the heartbeat rule before uploading', () => {
    expect(stationaryBranch()).toMatch(/shouldUploadFix\(/);
  });

  it('uploads the heartbeat when the rule says so', () => {
    const branch = stationaryBranch();
    // The upload must be reachable on the heartbeat path, not only in the
    // fallback path below the branch.
    expect(branch).toMatch(/await syncToGateway\(/);
  });

  it('records when it last uploaded, so the heartbeat can throttle', () => {
    expect(source).toMatch(/lastLocationUploadRef\.current = Date\.now\(\)/);
  });

  it('uploads unconditionally once the person is moving', () => {
    const start = source.indexOf('lastLocationRef.current = {');
    expect(start).toBeGreaterThan(-1);
    const tail = source.slice(start, start + 400);
    expect(tail).toMatch(/await syncToGateway\(/);
  });

  it('gets the radius from the tested lib, not a duplicated literal', () => {
    expect(source).not.toMatch(/const GEOFENCE_RADIUS_M = \d+/);
    expect(source).toMatch(/isInsideGeofence\(/);
  });
});

describe('LocationContext uploads the fix time and filters noisy fixes', () => {
  it('sends the fix time, not the send time', () => {
    expect(source).toMatch(/timestamp: \(fixTs \?\? Date\.now\(\)\) \/ 1000/);
    expect(source).toMatch(/syncToGateway\(latitude, longitude, accuracy \?\? null, speedMps, fixTs\)/);
  });
  it('runs every fix through classifyFix before uploading', () => {
    expect(source).toMatch(/const verdict = classifyFix\(/);
    expect(source).toMatch(/if \(verdict !== 'ok'\) return;/);
  });
});
