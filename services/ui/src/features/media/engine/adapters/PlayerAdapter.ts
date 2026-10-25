import type { PlayRequest, QueueOption } from '../../types';
import type { OutputId, OutputKind, RepeatMode } from '../../types';

export type TransportCommand = 'pause' | 'resume' | 'next' | 'previous' | 'stop';

/** What an output can do, so the UI hides controls it cannot honour. */
export interface Capabilities {
  queue: boolean;
  seek: boolean;
  shuffle: boolean;
  repeat: boolean;
  speed: boolean;
}

/**
 * One implementation per kind of output (§6.3). Screens never branch on
 * "is this the phone, a Music Assistant speaker or a Home Assistant entity";
 * they ask `adapterFor(outputId)` and call the same methods. Every method
 * throws a readable Error when the output refuses.
 */
export interface PlayerAdapter {
  kind: OutputKind;
  /** Replace what plays with `req`. */
  play(outputId: OutputId, req: PlayRequest): Promise<void>;
  /** Queue `req` after the current item ('next') or at the end ('add'). */
  enqueue(outputId: OutputId, req: PlayRequest, option: QueueOption): Promise<void>;
  transport(outputId: OutputId, command: TransportCommand): Promise<void>;
  seek(outputId: OutputId, seconds: number): Promise<void>;
  /** 0-100. */
  setVolume(outputId: OutputId, volume: number): Promise<void>;
  setMuted(outputId: OutputId, muted: boolean): Promise<void>;
  setShuffle(outputId: OutputId, on: boolean): Promise<void>;
  setRepeat(outputId: OutputId, mode: RepeatMode): Promise<void>;
  /** The queue's current shuffle/repeat, where the output can tell. */
  queueModes(outputId: OutputId): Promise<QueueModes>;
  capabilities(outputId: OutputId): Capabilities;
}

export interface QueueModes {
  shuffle: boolean | null;
  repeat: RepeatMode | null;
}

/** Shuffle/repeat out of a Music Assistant `player_queues/get` answer. */
export function parseQueueModes(raw: unknown): QueueModes {
  const record = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const mode = record.repeat_mode;
  return {
    shuffle: typeof record.shuffle_enabled === 'boolean' ? record.shuffle_enabled : null,
    repeat: mode === 'off' || mode === 'all' || mode === 'one' ? mode : null,
  };
}

/** Off → all → one → off. */
export function nextRepeat(mode: RepeatMode): RepeatMode {
  return mode === 'off' ? 'all' : mode === 'all' ? 'one' : 'off';
}

/** A Home Assistant / execution service reply that reports a refusal. */
export function assertSucceeded(resp: { status?: string; message?: string } | null | undefined, fallback: string): void {
  if (resp && resp.status === 'FAILURE') throw new Error(resp.message || fallback);
}
