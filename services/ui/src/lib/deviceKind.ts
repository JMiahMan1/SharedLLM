/**
 * The human name for a registered device's kind.
 *
 * The server's kinds are phone | tablet | assistant | light | watch (see
 * services/identity/models.py). A kind this UI does not know is reported as
 * "Unknown" rather than guessed at, so a new server kind never renders as,
 * say, "Phone".
 */
const LABELS: Record<string, string> = {
  phone: 'Phone',
  tablet: 'Tablet',
  watch: 'Watch',
  assistant: 'Assistant',
  light: 'Light',
};

export function deviceKindLabel(kind: string | null | undefined): string {
  const key = (kind ?? '').trim().toLowerCase();
  return LABELS[key] ?? 'Unknown';
}
