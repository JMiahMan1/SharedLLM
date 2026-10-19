import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Loader2, LogOut, Pin, PinOff, Trash2, UserMinus, UserPlus } from 'lucide-react';
import { api } from '../../services/api';
import PeoplePicker from './PeoplePicker';
import Sheet from './Sheet';
import UserAvatar from '../UserAvatar';
import { findFamilyMember, useFamilyDirectory } from './familyDirectory';
import { detailList, type TalkConversation, type TalkParticipant, type TalkPerson } from './chatModel';
import { errorText } from '../../lib/errorText';

/**
 * Everything about one conversation: who is in it, adding and removing
 * people, opening it on launch, and deleting (or leaving) it. Talk decides
 * who may do what; the sheet only offers what the conversation says you can.
 */
export default function ConversationInfo({
  conversation,
  myActorIds,
  isDefault,
  onToggleDefault,
  onGone,
  onClose,
}: {
  conversation: TalkConversation;
  myActorIds: ReadonlySet<string>;
  isDefault: boolean;
  onToggleDefault: () => void;
  onGone: () => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const people = useFamilyDirectory();
  const token = conversation.token;
  const group = conversation.type !== 1;
  const canModerate = Boolean(conversation.can_moderate);
  const [adding, setAdding] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const { data: members = [], isLoading } = useQuery({
    queryKey: ['talk-participants', token],
    queryFn: () => api.getTalkParticipants(token),
    select: (r) => detailList<TalkParticipant>(r, 'participants'),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['talk-participants', token] });
  const fail = (e: unknown) => toast.error(errorText(e, 'That did not work'));

  const add = useMutation({
    mutationFn: (p: TalkPerson) => api.addTalkParticipant(token, p.id),
    onSuccess: (_r, p) => {
      toast.success(`Added ${p.label}`);
      void refresh();
    },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (m: TalkParticipant) => api.removeTalkParticipant(token, m.attendee_id),
    onSuccess: (_r, m) => {
      toast.success(`Removed ${m.display_name}`);
      void refresh();
    },
    onError: fail,
  });
  const destroy = useMutation({
    mutationFn: () => api.deleteTalkConversation(token),
    onSuccess: (r) => {
      toast.success((r.detail as { deleted?: boolean } | undefined)?.deleted ? 'Conversation deleted' : 'You left the conversation');
      void queryClient.invalidateQueries({ queryKey: ['talk-conversations'] });
      onGone();
    },
    onError: fail,
  });

  const isMe = (m: TalkParticipant) => myActorIds.has(m.actor_id.toLowerCase());
  const leaveOnly = !canModerate;

  return (
    <Sheet title={conversation.display_name} onClose={onClose} testId="conversation-info">
      <button
        type="button"
        onClick={onToggleDefault}
        aria-pressed={isDefault}
        data-testid="chat-default-toggle"
        className="mb-4 flex w-full min-h-12 items-center gap-3 rounded-xl border border-white/10 px-3 text-left"
      >
        {isDefault ? <Pin size={16} className="text-purple-300" /> : <PinOff size={16} className="text-slate-500" />}
        <span className="flex-1">
          <span className="block text-sm font-semibold text-white">Open on launch</span>
          <span className="block text-xs text-slate-400">{isDefault ? 'Chat opens straight into this conversation.' : 'Open this conversation whenever Chat starts.'}</span>
        </span>
        <span className={`h-6 w-10 rounded-full p-0.5 transition ${isDefault ? 'bg-purple-500' : 'bg-white/15'}`}>
          <span className={`block h-5 w-5 rounded-full bg-white transition ${isDefault ? 'translate-x-4' : ''}`} />
        </span>
      </button>

      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">{members.length || ''} Members</h3>
        {group && canModerate && (
          <button type="button" onClick={() => setAdding(!adding)} className="min-h-10 rounded-xl px-2 text-xs text-purple-200 flex items-center gap-1" data-testid="chat-add-member">
            <UserPlus size={14} /> {adding ? 'Done' : 'Add people'}
          </button>
        )}
      </div>

      {adding && (
        <div className="mb-3 rounded-xl border border-white/10 p-2">
          <PeoplePicker selected={[]} onToggle={(p) => add.mutate(p)} exclude={members.map((m) => m.actor_id)} token={token} autoFocus />
        </div>
      )}

      {isLoading ? (
        <Loader2 size={16} className="animate-spin text-slate-500" />
      ) : (
        <ul className="mb-4 space-y-1" aria-label="Members">
          {members.map((m) => {
            const person = findFamilyMember(people, m.actor_id, m.display_name);
            return (
              <li key={m.attendee_id} className="flex min-h-12 items-center gap-3 rounded-xl px-2">
                <UserAvatar
                  username={person?.username ?? m.actor_id}
                  avatar={person?.avatar}
                  name={person?.display_name ?? m.display_name}
                  size={36}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-slate-100">
                    {m.display_name}
                    {isMe(m) ? ' (you)' : ''}
                  </span>
                  {m.is_moderator && <span className="block text-[11px] text-slate-500">Admin</span>}
                </span>
                {group && canModerate && !isMe(m) && m.actor_type === 'users' && (
                  <button
                    type="button"
                    onClick={() => remove.mutate(m)}
                    disabled={remove.isPending}
                    aria-label={`Remove ${m.display_name}`}
                    className="min-h-10 min-w-10 flex items-center justify-center rounded-xl text-slate-500 hover:text-rose-300"
                  >
                    <UserMinus size={15} />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {confirming ? (
        <div className="space-y-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3" role="alertdialog" aria-label="Confirm">
          <p className="text-sm text-rose-100">
            {leaveOnly
              ? 'Leave this conversation? It stays for everyone else.'
              : 'Delete this conversation for everyone? Its messages are gone for good.'}
          </p>
          <div className="flex gap-2">
            <button type="button" onClick={() => setConfirming(false)} className="min-h-11 flex-1 rounded-xl border border-white/10 text-sm text-slate-200">
              Cancel
            </button>
            <button
              type="button"
              onClick={() => destroy.mutate()}
              disabled={destroy.isPending}
              data-testid="chat-delete-confirm"
              className="min-h-11 flex-1 rounded-xl bg-rose-600 text-sm font-semibold text-white flex items-center justify-center gap-2"
            >
              {destroy.isPending && <Loader2 size={14} className="animate-spin" />}
              {leaveOnly ? 'Leave' : 'Delete'}
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          data-testid="chat-delete"
          className="flex w-full min-h-12 items-center justify-center gap-2 rounded-xl border border-rose-500/30 text-sm text-rose-200"
        >
          {leaveOnly ? <LogOut size={15} /> : <Trash2 size={15} />}
          {leaveOnly ? 'Leave conversation' : 'Delete conversation'}
        </button>
      )}
    </Sheet>
  );
}
