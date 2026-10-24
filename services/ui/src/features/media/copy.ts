/** Plain-language copy shared by the media screens (§3.5). */

/** Human copy for a service failure ("Music Assistant is unreachable"). */
export function serviceProblem(service: 'Music Assistant' | 'Audiobookshelf', message: string | null | undefined): string | null {
  if (!message) return null;
  if (/not configured/i.test(message)) return `${service} is not connected yet.`;
  return `${service} did not answer. ${message.replace(/^Music Assistant: |^Audiobookshelf: /, '')}`.trim();
}
