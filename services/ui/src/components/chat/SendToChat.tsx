import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2, Send } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { ExecutionResponse } from '../../types/api';
import ConversationAvatar from './ConversationAvatar';
import Sheet from './Sheet';
import { useFamilyDirectory } from './familyDirectory';
import { detailList, MAX_ATTACHMENT_BYTES, type TalkConversation } from './chatModel';

/** "data:image/png;base64,AAAA" -> its MIME type and base64 body. */
function splitDataUrl(dataUrl: string): { mime: string; base64: string } | null {
  const match = /^data:([^;,]+)(?:;[^,]*)?;base64,(.*)$/s.exec(dataUrl);
  return match ? { mime: match[1], base64: match[2] } : null;
}

/**
 * "Send to chat" for something made on the Create tab: pick a conversation
 * and it goes in as an attachment, the same as a photo from the composer.
 */
export default function SendToChat({
  getDataUrl,
  fileName,
  emptyMessage = 'Nothing to send yet',
  label = 'Send to chat',
}: {
  /** The thing to send, as a data URL ("" when there is nothing yet). */
  getDataUrl: () => string;
  fileName: string;
  emptyMessage?: string;
  label?: string;
}) {
  const [payload, setPayload] = useState<{ mime: string; base64: string } | null>(null);

  const open = () => {
    const parts = splitDataUrl(getDataUrl());
    if (!parts) {
      toast.error(emptyMessage);
      return;
    }
    if ((parts.base64.length * 3) / 4 > MAX_ATTACHMENT_BYTES) {
      toast.error('That is too big to send in a chat');
      return;
    }
    setPayload(parts);
  };

  return (
    <>
      <button type="button" onClick={open} className="glass-button min-h-11 px-3 py-2 text-sm">
        <Send size={14} /> {label}
      </button>
      {payload && <ConversationPicker payload={payload} fileName={fileName} onClose={() => setPayload(null)} />}
    </>
  );
}

function ConversationPicker({
  payload,
  fileName,
  onClose,
}: {
  payload: { mime: string; base64: string };
  fileName: string;
  onClose: () => void;
}) {
  const people = useFamilyDirectory();
  const [sendingTo, setSendingTo] = useState<string | null>(null);
  // Same query as the chat panel, so an open Family page answers from cache.
  const { data: conversations = [], isLoading } = useQuery<ExecutionResponse, Error, TalkConversation[]>({
    queryKey: ['talk-conversations'],
    queryFn: () => api.getTalkConversations(),
    select: (response) => detailList<TalkConversation>(response, 'conversations'),
  });

  const send = async (conversation: TalkConversation) => {
    setSendingTo(conversation.token);
    try {
      const res = await api.sendTalkFile({
        token: conversation.token,
        file_base64: payload.base64,
        mime_type: payload.mime,
        file_name: fileName,
      });
      if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'Could not send it');
      toast.success(`Sent to ${conversation.display_name || 'the chat'}`);
      onClose();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not send it');
    } finally {
      setSendingTo(null);
    }
  };

  return (
    <Sheet title="Send to…" onClose={onClose} testId="send-to-chat">
      {isLoading && <p className="py-6 text-center text-sm text-slate-400">Loading conversations…</p>}
      {!isLoading && conversations.length === 0 && (
        <p className="py-6 text-center text-sm text-slate-400">No conversations yet. Start one on the Chat tab.</p>
      )}
      <ul className="space-y-1">
        {conversations.map((conversation) => (
          <li key={conversation.token}>
            <button
              type="button"
              disabled={sendingTo !== null}
              onClick={() => void send(conversation)}
              className="flex min-h-14 w-full items-center gap-3 rounded-xl px-2 text-left hover:bg-white/5 disabled:opacity-60"
            >
              <ConversationAvatar conversation={conversation} people={people} size={40} />
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-100">
                {conversation.display_name || 'Conversation'}
              </span>
              {sendingTo === conversation.token && <Loader2 size={16} className="animate-spin text-slate-400" />}
            </button>
          </li>
        ))}
      </ul>
    </Sheet>
  );
}
