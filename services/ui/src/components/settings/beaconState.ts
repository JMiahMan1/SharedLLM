import type { BeaconStatus } from '../../plugins/bleBeacon';

/** One line saying what the beacon is doing. Never quiet about a failure. */
export function beaconStateLine(s: BeaconStatus): string {
  switch (s.state) {
    case 'advertising':
      return 'On: the house can tell which room this phone is in.';
    case 'starting':
      return 'Starting...';
    case 'bluetooth_off':
      return 'Waiting for Bluetooth: it starts again when Bluetooth is back on.';
    case 'disabled':
      return 'Off: the house does not know which room this phone is in.';
    case 'unavailable':
      return `Room detection unavailable: ${s.reason || 'the phone did not say why'}`;
    default:
      return `Room detection unavailable: unknown state ${String(s.state)}`;
  }
}
