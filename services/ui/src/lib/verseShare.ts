/**
 * A verse as a chat message: the same `📖 Reference: "text"` line the Family
 * chat's Verse chip posts, so a shared verse reads the same wherever it came
 * from (and stays plain text for anyone reading in Nextcloud Talk).
 */
/**
 * The person's own words (if any) above a shared message, the way every
 * share-to-chat message reads: note, blank line, then the shared thing.
 */
export function withShareNote(body: string, note?: string): string {
  const intro = (note ?? '').trim();
  return intro ? `${intro}\n\n${body}` : body;
}

export function verseShareText(reference: string, text: string, version?: string, note?: string): string {
  const label = version ? `${reference} (${version.toUpperCase()})` : reference;
  const line = `📖 ${label}: "${text.trim()}"`;
  return withShareNote(line, note);
}
