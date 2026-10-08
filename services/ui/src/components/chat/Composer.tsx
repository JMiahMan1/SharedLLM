import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  BarChart3,
  Camera,
  FileText,
  ImageIcon,
  Loader2,
  Mic,
  Paperclip,
  Pencil,
  Plus,
  Send,
  Sparkles,
  Square,
  X,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { ExecutionResponse } from '../../types/api';
import { formatBytes, MAX_ATTACHMENT_BYTES, plainText, type TalkMessage } from './chatModel';
import SendAsSelector from './SendAsSelector';
import type { SendAs } from './sendAsPref';

interface VoicePayload {
  audio_base64: string;
  mime_type: string;
  caption?: string;
}

interface MentionOption {
  id: string;
  label: string;
  jarvis?: boolean;
}

interface ComposerProps {
  token: string;
  disabled: boolean;
  replyTo: TalkMessage | null;
  onCancelReply: () => void;
  editing: TalkMessage | null;
  onCancelEdit: () => void;
  onSaveEdit: (text: string) => void;
  onSend: (text: string) => void;
  onSendVoice: (payload: VoicePayload) => Promise<boolean>;
  sendingVoice: boolean;
  staged: File[];
  onStage: (files: File[]) => void;
  onUnstage: (index: number) => void;
  onSendFiles: (caption: string) => Promise<boolean>;
  sendingFiles: boolean;
  onNewPoll: () => void;
  isAdmin: boolean;
  sendAs: SendAs;
  onSendAsChange: (next: SendAs) => void;
}

const MAX_INPUT_HEIGHT_PX = 140;
const JARVIS_OPTION: MentionOption = { id: 'Jarvis', label: 'Jarvis', jarvis: true };

/** The "@na" being typed just before the caret, if any. */
function mentionQuery(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const match = /(^|\s)@([\w.-]*)$/.exec(before);
  if (!match) return null;
  return { start: before.length - match[2].length - 1, query: match[2] };
}

/** Talk's mention syntax: @id, quoted when the id has spaces or symbols. */
function mentionToken(id: string): string {
  return /^[\w.-]+$/.test(id) ? `@${id}` : `@"${id}"`;
}

export default function Composer({
  token,
  disabled,
  replyTo,
  onCancelReply,
  editing,
  onCancelEdit,
  onSaveEdit,
  onSend,
  onSendVoice,
  sendingVoice,
  staged,
  onStage,
  onUnstage,
  onSendFiles,
  sendingFiles,
  onNewPoll,
  isAdmin,
  sendAs,
  onSendAsChange,
}: ComposerProps) {
  const [draft, setDraft] = useState('');
  const [caret, setCaret] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [recording, setRecording] = useState(false);
  const [clip, setClip] = useState<{ url: string; base64: string; mimeType: string } | null>(null);
  const [caption, setCaption] = useState('');
  const [mentionOptions, setMentionOptions] = useState<MentionOption[]>([]);
  const [mentionIndex, setMentionIndex] = useState(0);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const photoRef = useRef<HTMLInputElement | null>(null);
  const cameraRef = useRef<HTMLInputElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  // Grow with the text, up to a few lines, like every mobile messenger.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT_PX)}px`;
  }, [draft]);

  useEffect(() => {
    if (replyTo) inputRef.current?.focus();
  }, [replyTo]);

  // Entering edit mode loads the message into the box; leaving it clears it.
  const editingId = editing?.id;
  useEffect(() => {
    if (editingId === undefined) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- seeding the editor from the chosen message
    setDraft(plainText(editing?.message));
    inputRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when a different message is chosen
  }, [editingId]);

  const mention = useMemo(() => mentionQuery(draft, caret), [draft, caret]);

  // Suggestions: Jarvis first, then the people Talk can mention in this room.
  useEffect(() => {
    if (!mention || !token) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- closing the suggestion list
      setMentionOptions([]);
      return;
    }
    const q = mention.query.toLowerCase();
    const base = 'jarvis'.startsWith(q) ? [JARVIS_OPTION] : [];
    setMentionOptions(base);
    setMentionIndex(0);
    let live = true;
    const timer = window.setTimeout(async () => {
      try {
        const res: ExecutionResponse = await api.getTalkMentions(token, mention.query);
        const list = ((res.detail as { mentions?: Array<{ id?: string; label?: string; mention_id?: string }> } | undefined)?.mentions || [])
          .filter((m) => m.id && m.id !== 'all')
          .map((m) => ({ id: String(m.mention_id || m.id), label: String(m.label || m.id) }));
        if (live) setMentionOptions([...base, ...list].slice(0, 6));
      } catch {
        // Suggestions are a convenience; typing the name still works.
      }
    }, 200);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [mention, token]);

  const pickMention = (option: MentionOption) => {
    if (!mention) return;
    const before = draft.slice(0, mention.start);
    const after = draft.slice(caret);
    let next: string;
    let nextCaret: number;
    if (option.jarvis) {
      // Jarvis answers messages that *start* with @Jarvis, wherever you typed it.
      const rest = `${before}${after}`.trim();
      next = `@Jarvis ${rest}`;
      nextCaret = next.length;
    } else {
      const insert = `${mentionToken(option.id)} `;
      next = `${before}${insert}${after}`;
      nextCaret = before.length + insert.length;
    }
    setDraft(next);
    setMentionOptions([]);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(nextCaret, nextCaret);
      setCaret(nextCaret);
    });
  };

  const pickPhoto = () => photoRef.current?.click();
  const pickCamera = () => cameraRef.current?.click();
  const pickFile = () => fileRef.current?.click();

  const askJarvis = () => {
    setMenuOpen(false);
    if (!/^\s*@jarvis\b/i.test(draft)) setDraft(`@Jarvis ${draft.trimStart()}`);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const stage = (files: FileList | File[] | null) => {
    const list = Array.from(files || []);
    const tooBig = list.filter((f) => f.size > MAX_ATTACHMENT_BYTES);
    if (tooBig.length) toast.error(`${tooBig.map((f) => f.name).join(', ')} is over 25 MB`);
    const ok = list.filter((f) => f.size <= MAX_ATTACHMENT_BYTES);
    if (ok.length) onStage(ok);
  };

  const submit = async () => {
    const text = draft.trim();
    if (disabled) return;
    if (editing) {
      if (text) onSaveEdit(text);
      setDraft('');
      return;
    }
    if (staged.length) {
      if (await onSendFiles(text)) setDraft('');
      return;
    }
    if (!text) return;
    onSend(text);
    setDraft('');
  };

  const startRecording = async () => {
    if (disabled) {
      toast.error('Pick a conversation first');
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      toast.error('Recording is not available on this device');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        if (blob.size === 0) return;
        const bytes = new Uint8Array(await blob.arrayBuffer());
        let binary = '';
        for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
        // Preview before sending: recording is easy to do by accident, and a
        // caption often carries the point of a voice note.
        setClip({ url: URL.createObjectURL(blob), base64: btoa(binary), mimeType: blob.type });
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      toast.error('Microphone permission is needed to record a voice message');
    }
  };

  const stopRecording = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  };

  const discardClip = () => {
    if (clip) URL.revokeObjectURL(clip.url);
    setClip(null);
    setCaption('');
  };

  const sendClip = async () => {
    if (!clip) return;
    const sent = await onSendVoice({ audio_base64: clip.base64, mime_type: clip.mimeType, caption: caption.trim() });
    if (sent) discardClip();
  };

  const hasText = draft.trim().length > 0;
  const canSend = hasText || staged.length > 0;
  const adminMode = isAdmin && sendAs === 'admin';
  const toJarvis = /^\s*@jarvis\b/i.test(draft);

  return (
    <div
      className="shrink-0 border-t border-white/5 bg-slate-950/80 px-2 pt-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] backdrop-blur sm:px-3"
      onPaste={(event) => {
        const files = Array.from(event.clipboardData?.files || []);
        if (files.length) {
          event.preventDefault();
          stage(files);
        }
      }}
    >
      <input ref={photoRef} type="file" accept="image/*,video/*" multiple hidden onChange={(e) => { stage(e.target.files); e.target.value = ''; }} />
      <input ref={cameraRef} type="file" accept="image/*" capture="environment" hidden onChange={(e) => { stage(e.target.files); e.target.value = ''; }} />
      <input ref={fileRef} type="file" multiple hidden aria-label="Attach files" onChange={(e) => { stage(e.target.files); e.target.value = ''; }} />

      {editing && (
        <div className="mb-2 flex items-center gap-2 rounded-xl border-l-2 border-amber-400 bg-white/5 px-3 py-1.5" data-testid="edit-preview">
          <Pencil size={13} className="text-amber-300" />
          <p className="flex-1 text-[11px] font-semibold text-amber-200">Editing message</p>
          <button
            type="button"
            aria-label="Cancel edit"
            className="p-1 text-slate-400 hover:text-slate-100"
            onClick={() => {
              setDraft('');
              onCancelEdit();
            }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      {replyTo && !editing && (
        <div className="mb-2 flex items-center gap-2 rounded-xl border-l-2 border-purple-400 bg-white/5 px-3 py-1.5" data-testid="reply-preview">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-semibold text-purple-200">Replying to {replyTo.actor_display_name || 'message'}</p>
            <p className="truncate text-xs text-slate-400">{plainText(replyTo.message)}</p>
          </div>
          <button type="button" aria-label="Cancel reply" className="p-1 text-slate-400 hover:text-slate-100" onClick={onCancelReply}>
            <X size={14} />
          </button>
        </div>
      )}

      {staged.length > 0 && (
        <div className="mb-2 flex gap-2 overflow-x-auto pb-1" data-testid="attachment-tray">
          {staged.map((file, index) => (
            <StagedFile key={`${file.name}-${index}`} file={file} onRemove={() => onUnstage(index)} />
          ))}
        </div>
      )}

      {clip && (
        <div className="mb-2 space-y-2 rounded-2xl border border-white/10 bg-white/5 p-2" data-testid="voice-preview">
          <p className="text-[11px] text-slate-300">Recorded clip ready</p>
          <audio controls src={clip.url} className="h-9 w-full" />
          <input
            value={caption}
            onChange={(event) => setCaption(event.target.value)}
            placeholder="Add a caption (optional)"
            aria-label="Voice message caption"
            className="glass-input w-full text-base sm:text-sm"
          />
          <div className="flex gap-2">
            <button type="button" className="glass-button flex-1 min-h-11 px-3 py-2 text-sm" disabled={sendingVoice} onClick={() => void sendClip()}>
              {sendingVoice ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />} Send voice
            </button>
            <button type="button" className="glass-button px-3 py-2 text-sm" onClick={discardClip}>
              Discard
            </button>
          </div>
        </div>
      )}

      {isAdmin && (
        <div className="mb-1.5 flex items-center gap-2 px-1">
          <SendAsSelector value={sendAs} onChange={onSendAsChange} />
          {adminMode && <span className="text-[10px] text-slate-500">in this chat only</span>}
        </div>
      )}

      <div className="relative flex items-end gap-1.5">
        {mentionOptions.length > 0 && (
          <ul
            className="absolute bottom-14 left-12 z-20 w-64 overflow-hidden rounded-2xl border border-white/10 bg-slate-900 shadow-xl"
            role="listbox"
            aria-label="Mention suggestions"
          >
            {mentionOptions.map((option, index) => (
              <li key={`${option.id}-${index}`}>
                <button
                  type="button"
                  role="option"
                  aria-selected={index === mentionIndex}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => pickMention(option)}
                  className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${index === mentionIndex ? 'bg-white/10' : 'hover:bg-white/5'}`}
                >
                  {option.jarvis ? (
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-fuchsia-500 to-indigo-500 text-white">
                      <Sparkles size={12} />
                    </span>
                  ) : (
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white/10 text-[10px] font-bold">
                      {option.label.slice(0, 2).toUpperCase()}
                    </span>
                  )}
                  <span className="flex-1 truncate text-slate-100">{option.label}</span>
                  {option.jarvis && <span className="text-[10px] text-fuchsia-300">AI assistant</span>}
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="relative">
          <button
            type="button"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-full text-slate-300 hover:bg-white/10"
            aria-label="More options"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((v) => !v)}
            disabled={Boolean(editing)}
          >
            <Plus size={20} className={`transition ${menuOpen ? 'rotate-45' : ''}`} />
          </button>
          {menuOpen && (
            <div className="absolute bottom-12 left-0 z-20 w-52 rounded-2xl border border-white/10 bg-slate-900 p-1 shadow-xl" role="menu">
              <MenuItem icon={<Sparkles size={15} className="text-fuchsia-300" />} label="Ask Jarvis" onSelect={askJarvis} close={() => setMenuOpen(false)} />
              <MenuItem icon={<ImageIcon size={15} className="text-sky-300" />} label="Photo or video" onSelect={pickPhoto} close={() => setMenuOpen(false)} />
              <MenuItem icon={<Camera size={15} className="text-emerald-300" />} label="Camera" onSelect={pickCamera} close={() => setMenuOpen(false)} />
              <MenuItem icon={<Paperclip size={15} className="text-amber-300" />} label="File" onSelect={pickFile} close={() => setMenuOpen(false)} />
              <MenuItem icon={<BarChart3 size={15} className="text-fuchsia-300" />} label="New poll" onSelect={onNewPoll} close={() => setMenuOpen(false)} />
            </div>
          )}
        </div>

        <textarea
          ref={inputRef}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setCaret(event.target.selectionStart ?? event.target.value.length);
          }}
          onSelect={(event) => setCaret((event.target as HTMLTextAreaElement).selectionStart ?? 0)}
          onKeyDown={(event) => {
            if (mentionOptions.length > 0) {
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                const step = event.key === 'ArrowDown' ? 1 : -1;
                setMentionIndex((i) => (i + step + mentionOptions.length) % mentionOptions.length);
                return;
              }
              if (event.key === 'Enter' || event.key === 'Tab') {
                event.preventDefault();
                pickMention(mentionOptions[mentionIndex]);
                return;
              }
              if (event.key === 'Escape') {
                setMentionOptions([]);
                return;
              }
            }
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void submit();
            }
            if (event.key === 'Escape') {
              if (editing) {
                setDraft('');
                onCancelEdit();
              } else if (replyTo) onCancelReply();
            }
          }}
          rows={1}
          placeholder={
            editing ? 'Edit message' : staged.length ? 'Add a caption' : adminMode ? 'Message as Admin' : 'Message, or @Jarvis to ask'
          }
          aria-label="Message"
          className={`min-h-11 flex-1 resize-none rounded-3xl border bg-white/5 px-4 py-2.5 text-base leading-snug text-slate-100 placeholder:text-slate-500 focus:outline-none sm:text-[15px] ${
            toJarvis
              ? 'border-fuchsia-400/50 focus:border-fuchsia-400/80'
              : adminMode
                ? 'border-amber-400/40 focus:border-amber-400/70'
                : 'border-white/10 focus:border-purple-400/50'
          }`}
        />

        {canSend || editing ? (
          <button
            type="button"
            className={`flex min-h-11 min-w-11 items-center justify-center rounded-full text-white transition disabled:opacity-50 ${
              toJarvis ? 'bg-gradient-to-br from-fuchsia-500 to-indigo-500' : 'bg-purple-600 hover:bg-purple-500'
            }`}
            onClick={() => void submit()}
            disabled={disabled || sendingFiles || (Boolean(editing) && !hasText)}
            aria-label={editing ? 'Save edit' : toJarvis ? 'Ask Jarvis' : 'Send message'}
          >
            {sendingFiles ? <Loader2 size={18} className="animate-spin" /> : toJarvis ? <Sparkles size={18} /> : <Send size={18} />}
          </button>
        ) : (
          <button
            type="button"
            className={`flex min-h-11 min-w-11 items-center justify-center rounded-full transition ${
              recording ? 'animate-pulse bg-rose-500 text-white' : 'text-slate-300 hover:bg-white/10'
            }`}
            onClick={() => (recording ? stopRecording() : void startRecording())}
            disabled={sendingVoice}
            aria-label={recording ? 'Stop recording' : 'Record a voice message'}
            aria-pressed={recording}
          >
            {recording ? <Square size={16} /> : <Mic size={20} />}
          </button>
        )}
      </div>
    </div>
  );
}

function MenuItem({ icon, label, onSelect, close }: { icon: ReactNode; label: string; onSelect: () => void; close: () => void }) {
  return (
    <button
      type="button"
      role="menuitem"
      className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm text-slate-100 hover:bg-white/10"
      onClick={() => {
        close();
        onSelect();
      }}
    >
      {icon} {label}
    </button>
  );
}

function StagedFile({ file, onRemove }: { file: File; onRemove: () => void }) {
  const isImage = file.type.startsWith('image/');
  const url = useMemo(() => (isImage ? URL.createObjectURL(file) : null), [file, isImage]);
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);
  return (
    <div className="relative shrink-0">
      {url ? (
        <img src={url} alt={file.name} className="h-20 w-20 rounded-xl object-cover" />
      ) : (
        <div className="flex h-20 w-40 flex-col justify-center rounded-xl bg-white/10 px-3">
          <FileText size={16} className="text-slate-300" />
          <span className="mt-1 truncate text-xs text-slate-100">{file.name}</span>
          <span className="text-[10px] text-slate-400">{formatBytes(file.size)}</span>
        </div>
      )}
      <button
        type="button"
        aria-label={`Remove ${file.name}`}
        onClick={onRemove}
        className="absolute -right-1.5 -top-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-slate-800 text-slate-200 shadow"
      >
        <X size={12} />
      </button>
    </div>
  );
}
