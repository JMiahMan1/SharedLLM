/**
 * Typed chat envelope.
 *
 * A bot message (achievement, game move, drawing) is posted to Talk as plain
 * text, so the envelope has to survive a round-trip through a string field and
 * still be invisible to anyone whose client cannot render it. The wire format
 * is therefore a single fenced JSON block *appended* to human-readable text:
 * old clients show the text, Jarvis reads the block.
 *
 * Anything malformed degrades to plain text rather than throwing — a bad card
 * must never blank out a conversation.
 */

/** `assistant` marks a Jarvis answer: drawn as Jarvis's own bubble, no card. */
export type EnvelopeKind = 'text' | 'activity' | 'game' | 'creation' | 'system' | 'assistant';

export interface ChatEnvelope {
  kind: EnvelopeKind;
  /** Short headline, e.g. "First 10k steps". */
  title: string;
  /** Optional supporting line shown under the title. */
  detail?: string;
  /** Activity/game numbers worth showing as tiles. */
  stats?: Array<{ label: string; value: string }>;
  /** Star amount for achievement envelopes. */
  stars?: number;
  /** Avatar-free link (game to play, drawing to open). */
  href?: string;
  action?: { label: string; href: string };
}

const FENCE = '```jarvis-envelope';

const KINDS: EnvelopeKind[] = ['text', 'activity', 'game', 'creation', 'system', 'assistant'];

/** Append an envelope to human-readable text. */
export function encodeEnvelope(text: string, envelope: ChatEnvelope): string {
  if (envelope.kind === 'text') return text;
  return `${text.trim()}\n\n${FENCE}\n${JSON.stringify(envelope)}\n${FENCE}`;
}

/**
 * Split a raw message into its readable text and optional envelope.
 * Never throws: unreadable envelopes are dropped, not surfaced.
 */
export function decodeEnvelope(raw: string | null | undefined): { text: string; envelope: ChatEnvelope | null } {
  const message = String(raw ?? '');
  const start = message.indexOf(FENCE);
  if (start === -1) return { text: message, envelope: null };

  const bodyStart = start + FENCE.length;
  const end = message.indexOf(FENCE, bodyStart);
  if (end === -1) return { text: message, envelope: null };

  const text = message.slice(0, start).trimEnd();
  try {
    const parsed = JSON.parse(message.slice(bodyStart, end).trim()) as Partial<ChatEnvelope>;
    if (!parsed || typeof parsed !== 'object' || !KINDS.includes(parsed.kind as EnvelopeKind)) {
      return { text, envelope: null };
    }
    if (typeof parsed.title !== 'string' || !parsed.title.trim()) {
      return { text, envelope: null };
    }
    const envelope: ChatEnvelope = { kind: parsed.kind as EnvelopeKind, title: parsed.title.trim() };
    if (typeof parsed.detail === 'string') envelope.detail = parsed.detail;
    if (Array.isArray(parsed.stats)) {
      const stats = parsed.stats
        .filter((s) => s && typeof s.label === 'string' && typeof s.value === 'string')
        .map((s) => ({ label: s.label, value: s.value }));
      if (stats.length) envelope.stats = stats;
    }
    if (typeof parsed.stars === 'number' && Number.isFinite(parsed.stars)) envelope.stars = parsed.stars;
    if (typeof parsed.href === 'string') envelope.href = parsed.href;
    if (parsed.action && typeof parsed.action === 'object') {
      const { label, href } = parsed.action as { label?: unknown; href?: unknown };
      if (typeof label === 'string' && typeof href === 'string') envelope.action = { label, href };
    }
    return { text, envelope };
  } catch {
    // A malformed card is not worth breaking a conversation over.
    return { text, envelope: null };
  }
}

/** Achievement envelope for the family's star/points work. */
export function activityEnvelope(input: {
  title: string;
  detail?: string;
  stars?: number;
  stats?: Array<{ label: string; value: string }>;
}): ChatEnvelope {
  return { kind: 'activity', ...input };
}
