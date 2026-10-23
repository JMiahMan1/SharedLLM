import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { MessageCircle, Send, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import ConversationAvatar from '../chat/ConversationAvatar';
import { detailList, type TalkConversation } from '../chat/chatModel';
import { useFamilyDirectory } from '../chat/familyDirectory';
import { verseShareText } from '../../lib/verseShare';
import { errorText } from '../../lib/errorText';

/**
 * Send a verse straight into a Family chat conversation: pick who, optionally
 * say something, send. Posts through the same Talk path as the chat itself.
 */
export default function ShareToChatSheet({
  reference,
  text,
  version,
  heading,
  previewIcon = '📖',
  buildMessage,
  onClose,
  onSent,
}: {
  reference: string;
  text: string;
  version?: string;
  /** Sheet title; defaults to the verse phrasing. */
  heading?: string;
  /** Emoji for the preview line (a verse from the Bible reader, a plate from Recipes). */
  previewIcon?: string;
  /** Override for the outgoing message body. */
  buildMessage?: (note: string) => string;
  onClose: () => void;
  onSent?: () => void;
}) {
  const sheetTitle = heading ?? `Share ${reference} to chat`;
  const navigate = useNavigate();
  const people = useFamilyDirectory();
  const [note, setNote] = useState('');
  const [chosen, setChosen] = useState<string | null>(null);
  const { data: conversations = [], isLoading, isError } = useQuery({
    queryKey: ['talk-conversations'],
    queryFn: () => api.getTalkConversations(),
    select: (response) => detailList<TalkConversation>(response, 'conversations'),
  });
  const send = useMutation({
    mutationFn: (token: string) =>
      api.sendTalkMessage({ token, message: buildMessage ? buildMessage(note) : verseShareText(reference, text, version, note) }),
    onSuccess: (response, token) => {
      if (response.status && response.status !== 'SUCCESS') {
        toast.error(response.message || 'The message could not be sent');
        return;
      }
      const name = conversations.find((c) => c.token === token)?.display_name ?? 'the chat';
      onSent?.();
      onClose();
      toast.success(
        (t) => (
          <span className="flex items-center gap-3">
            Sent to {name}
            <button
              type="button"
              className="rounded-md bg-white/10 px-2 py-1 text-xs"
              onClick={() => {
                toast.dismiss(t.id);
                navigate(`/family?chat=${encodeURIComponent(token)}`);
              }}
            >
              Open chat
            </button>
          </span>
        ),
      );
    },
    onError: (e) => toast.error(errorText(e, 'The message could not be sent')),
  });

  return (
    <div className="fixed inset-0 z-[60] flex items-end sm:items-center sm:justify-center" role="dialog" aria-modal="true" aria-label={sheetTitle}>
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative w-full sm:max-w-md bg-slate-950/97 border border-white/10 border-b-0 sm:border-b sm:rounded-2xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))] max-h-[85vh] flex flex-col" data-testid="bible-share-chat-sheet">
        <div className="flex items-center justify-between gap-3 mb-3">
          <p className="text-sm font-semibold text-white flex items-center gap-2">
            <MessageCircle size={16} className="text-sky-300" /> {sheetTitle}
          </p>
          <button type="button" onClick={onClose} aria-label="Close" className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-400 hover:text-white">
            <X size={16} />
          </button>
        </div>

        <p className="text-xs text-slate-300 rounded-xl bg-white/5 border border-white/10 p-3 mb-3 line-clamp-4">
          {previewIcon} <span className="font-semibold text-amber-200">{reference}</span>: “{text}”
        </p>
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Add a message (optional)"
          aria-label="Add a message"
          className="glass-input w-full text-sm mb-3 min-h-11"
          data-testid="bible-share-chat-note"
        />

        <div className="flex-1 overflow-y-auto -mx-1" data-testid="bible-share-chat-list">
          {isLoading && <p className="px-1 py-3 text-sm text-slate-400">Loading conversations…</p>}
          {isError && <p className="px-1 py-3 text-sm text-red-300">Could not load your conversations.</p>}
          {!isLoading && !isError && conversations.length === 0 && (
            <p className="px-1 py-3 text-sm text-slate-400">No conversations yet. Start one in Family › Chat.</p>
          )}
          {conversations.map((c) => (
            <button
              key={c.token}
              type="button"
              disabled={send.isPending}
              onClick={() => {
                setChosen(c.token);
                send.mutate(c.token);
              }}
              className="w-full flex items-center gap-3 px-1 py-2 rounded-xl text-left hover:bg-white/5 disabled:opacity-60 min-h-11"
              data-testid="bible-share-chat-target"
            >
              <ConversationAvatar conversation={c} people={people} size={40} />
              <span className="flex-1 min-w-0 truncate text-sm text-white">{c.display_name}</span>
              {send.isPending && chosen === c.token ? (
                <span className="text-xs text-slate-400">Sending…</span>
              ) : (
                <Send size={16} className="text-sky-300 shrink-0" />
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
