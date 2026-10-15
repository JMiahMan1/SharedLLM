/**
 * A verse as a chat message: the same `📖 Reference — "text"` line the Family
 * chat's Verse chip posts, so a shared verse reads the same wherever it came
 * from (and stays plain text for anyone reading in Nextcloud Talk).
 */
export function verseShareText(reference: string, text: string, version?: string, note?: string): string {
  const label = version ? `${reference} (${version.toUpperCase()})` : reference;
  const line = `📖 ${label} — "${text.trim()}"`;
  const intro = (note ?? '').trim();
  return intro ? `${intro}\n\n${line}` : line;
}
