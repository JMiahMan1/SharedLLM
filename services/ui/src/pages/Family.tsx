import { useState } from 'react';
import {
  BookOpen,
  Brush,
  Gamepad2,
  MessageCircle,
  Music,
  Sparkles,
  UtensilsCrossed,
} from 'lucide-react';
import ChatPanel from '../components/chat/ChatPanel';

type Tab = 'chat' | 'games' | 'create';

const TABS: Array<{ id: Tab; label: string; icon: typeof MessageCircle }> = [
  { id: 'chat', label: 'Chat', icon: MessageCircle },
  { id: 'games', label: 'Games', icon: Gamepad2 },
  { id: 'create', label: 'Create', icon: Brush },
];

/** Roadmap cards are honest: they say what is coming, not fake a feature. */
const GAMES = [
  { icon: BookOpen, name: 'Bible Trivia', detail: 'Multiple choice, any age, gentle scoring' },
  { icon: BookOpen, name: 'Bible Memory Verses', detail: 'Fill-in-the-blank drills with hints' },
  { icon: Gamepad2, name: 'Family Trivia', detail: 'Custom questions about your own family' },
  { icon: Sparkles, name: 'Draw & Guess', detail: 'One draws, everyone guesses in chat' },
];

const CREATE = [
  { icon: Brush, name: 'Draw together', detail: 'Shared canvas, then ask Jarvis to improve it' },
  { icon: Sparkles, name: 'Make a picture', detail: 'Describe it and Jarvis/Raven paints it' },
  { icon: Music, name: 'Make music', detail: 'Turn a mood or a phrase into a song to share' },
  { icon: UtensilsCrossed, name: 'Family recipes', detail: 'Collect dinner ideas the whole house can edit' },
];

/**
 * Family hub: chat, games and making things together.
 *
 * Chat is live on Nextcloud Talk (the same rooms as the Nextcloud app) with
 * text and voice messages. Games and Create are the next slices — see
 * docs/FAMILY_HUB.md — and are shown here as a roadmap rather than dead
 * buttons, so nobody taps something that does nothing.
 */
export default function Family() {
  const [tab, setTab] = useState<Tab>('chat');

  return (
    <div className="space-y-4 sm:space-y-5 max-w-6xl mx-auto pb-28 px-1 sm:px-0" data-testid="family-page">
      <div className="glass-panel p-4 sm:p-5 rounded-2xl border border-white/10">
        <h1 className="text-xl sm:text-2xl font-bold text-slate-100">Family</h1>
        <p className="text-xs text-slate-400 mt-0.5">
          Talk, play and make things together — for every age in the house.
        </p>
        <div className="mt-3 sm:mt-4 grid grid-cols-3 gap-2 sm:flex sm:flex-wrap" role="tablist" aria-label="Family sections">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`glass-button px-3 sm:px-4 py-2.5 text-sm min-h-11 justify-center ${tab === id ? 'text-purple-200 border-purple-400/50' : 'text-slate-300'}`}
            >
              <Icon size={15} /> {label}
            </button>
          ))}
        </div>
      </div>

      {tab === 'chat' && <ChatPanel />}

      {tab === 'games' && (
        <div className="space-y-4" data-testid="family-games">
          <div className="glass-panel p-4 rounded-2xl border border-white/5">
            <h2 className="text-sm font-semibold text-slate-200">Games</h2>
            <p className="text-xs text-slate-400 mt-1">
              Games run in the same conversation as chat, so scores and cheers land where the family already is.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {GAMES.map(({ icon: Icon, name, detail }) => (
              <div key={name} className="glass-panel p-4 rounded-2xl border border-white/5">
                <div className="flex items-center gap-2">
                  <Icon size={16} className="text-purple-300" />
                  <span className="font-semibold text-slate-100">{name}</span>
                  <span className="ml-auto text-[10px] uppercase tracking-wider text-slate-500">planned</span>
                </div>
                <p className="text-xs text-slate-400 mt-2">{detail}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === 'create' && (
        <div className="space-y-4" data-testid="family-create">
          <div className="glass-panel p-4 rounded-2xl border border-white/5">
            <h2 className="text-sm font-semibold text-slate-200">Create</h2>
            <p className="text-xs text-slate-400 mt-1">
              Drawings, pictures, songs and recipes live in shared family space, and anything you make can be sent
              straight into a conversation.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {CREATE.map(({ icon: Icon, name, detail }) => (
              <div key={name} className="glass-panel p-4 rounded-2xl border border-white/5">
                <div className="flex items-center gap-2">
                  <Icon size={16} className="text-purple-300" />
                  <span className="font-semibold text-slate-100">{name}</span>
                  <span className="ml-auto text-[10px] uppercase tracking-wider text-slate-500">planned</span>
                </div>
                <p className="text-xs text-slate-400 mt-2">{detail}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
