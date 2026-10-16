import { useState, useRef, useCallback, useEffect } from 'react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../services/api';
import type { IWidgetProps } from '../../types/widget';
import { chatReplyText } from '../../lib/chatReply';
import toast from 'react-hot-toast';


interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
}

const QuickAssistantWidget = ({ settingsButton }: IWidgetProps) => {
  const { user } = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Scroll the message list only. scrollIntoView would also scroll the whole
  // dashboard to this card on every reply.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: text,
      timestamp: Date.now(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setSending(true);

    try {
      const resp = await api.chat(text, undefined, user?.username);
      const failed = (resp as { status?: string } | null)?.status === 'ERROR';
      const content = chatReplyText(resp) ?? (failed
        ? 'The assistant could not process that request.'
        : 'No response received');

      setMessages((prev) => [
        ...prev,
        { id: `assistant-${Date.now()}`, role: 'assistant', content, timestamp: Date.now() },
      ]);
    } catch {
      toast.error('Failed to send message');
      setMessages((prev) => prev.filter((m) => m.id !== userMessage.id));
      setInput(text);
    } finally {
      setSending(false);
    }
  }, [input, sending, user]);

  return (
    <div className="glass-card h-full p-5 relative flex flex-col">
      <div className="absolute top-3 right-3 z-10">{settingsButton}</div>
      <h3 className="font-bold text-white text-lg mb-4 pr-8">Quick Assistant</h3>

      {messages.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-center">
          <div>
            <p className="text-sm text-slate-400 mb-2">Ask me anything</p>
            <p className="text-xs text-slate-500">Type a message to start chatting</p>
          </div>
        </div>
      ) : (
        <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto space-y-3 mb-4 pr-1">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[85%] rounded-lg px-3 py-2 ${
                  msg.role === 'user'
                    ? 'bg-indigo-600 text-white'
                    : 'bg-slate-800/50 border border-slate-700/50 text-white'
                }`}
              >
                <p className="text-sm whitespace-pre-wrap break-words">{msg.content}</p>
                <p className={`text-xs mt-1 ${msg.role === 'user' ? 'text-indigo-200' : 'text-slate-500'}`}>
                  {new Date(msg.timestamp).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
                </p>
              </div>
            </div>
          ))}
          {sending && <p className="text-xs text-slate-500 animate-pulse">Thinking…</p>}
        </div>
      )}

      {/* Enter submits the form. No autofocus: on a phone that popped the
          keyboard over the dashboard every time it opened. */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void handleSend();
        }}
        className="flex gap-2 mt-auto"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message..."
          aria-label="Message the assistant"
          disabled={sending}
          className="flex-1 min-w-0 min-h-11 bg-slate-800/50 border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-slate-600/50 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="min-h-11 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        >
          {sending ? '...' : 'Send'}
        </button>
      </form>
    </div>
  );
};

export default QuickAssistantWidget;
