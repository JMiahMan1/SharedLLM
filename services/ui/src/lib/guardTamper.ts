/**
 * A child's phone's tamper reports in plain words, for the father's Devices
 * section (Phase 11): protection switched off, and the ways around the web
 * filter the guard found. The event names are the gateway's `_TAMPER_EVENTS`.
 */
const TAMPER_WORDS: Record<string, string> = {
  admin_disabled: "Jarvis's device protection was turned off",
  guard_disabled: "Jarvis's screen guard was turned off",
  guard_missing: "Jarvis's screen guard was missing",
  uninstall_attempt: 'Someone tried to remove Jarvis',
  offline_unlock: "Unlocked with a parent's offline code",
  filter_disabled: "Jarvis's web filter was turned off",
  bypass_app: 'An app that gets around the web filter (closed while it is on)',
  private_dns: 'Private DNS set to its own provider, around the web filter',
  private_dns_attempt: 'Tried to change Private DNS',
  other_vpn: "Another VPN ran in place of Jarvis's web filter",
  boot_without_guard: "Started without Jarvis's guard (safe mode, most likely)",
};

/** One tamper report in words, with what it was about when the phone said (the app, the DNS host). */
export function tamperText(extra: { event?: string; detail?: string } | undefined): string {
  const words = TAMPER_WORDS[extra?.event || ''] || extra?.event || 'Protection changed';
  return extra?.detail ? `${words}: ${extra.detail}` : words;
}
