import { useCallback, useEffect, useRef, useState } from 'react';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { storageGetSync } from '../../../lib/storage';
import type { Workspace } from '../../../services/api';
import '@xterm/xterm/css/xterm.css';

interface TerminalPaneProps {
  workspace: Workspace;
}

interface ContextMenuState {
  visible: boolean;
  x: number;
  y: number;
}

type MenuItem = {
  label: string;
  shortcut?: string;
  disabled?: boolean;
  danger?: boolean;
  action: () => void;
};

export function TerminalPane({ workspace }: TerminalPaneProps) {
  const ref = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuPos, setMenuPos] = useState<ContextMenuState>({
    visible: false,
    x: 0,
    y: 0,
  });
  const [menuItems, setMenuItems] = useState<MenuItem[]>([]);
  const mountedRef = useRef(true);

  // Close menu when clicking outside
  useEffect(() => {
    const handleClick = () => {
      if (menuRef.current && !menuRef.current.contains(document.activeElement)) {
        setMenuPos(prev => ({ ...prev, visible: false }));
      }
    };
    if (menuPos.visible) {
      document.addEventListener('mousedown', handleClick);
    }
    return () => document.removeEventListener('mousedown', handleClick);
  }, [menuPos.visible]);

  const handleContextMenu = useCallback((e: Event) => {
    e.preventDefault();
    const event = e as MouseEvent;
    
    const term = termRef.current;
    const ws = wsRef.current;
    const isConnected = ws?.readyState === WebSocket.OPEN;
    const hasSelection = term?.getSelection()?.length || 0;

    const items: MenuItem[] = [
      {
        label: 'Copy',
        shortcut: 'Ctrl+C',
        action: () => {
          const sel = term?.getSelection();
          if (sel) {
            navigator.clipboard.writeText(sel);
            term?.clearSelection();
          }
        },
        disabled: !hasSelection,
      },
      {
        label: 'Paste',
        shortcut: 'Ctrl+Shift+V',
        action: async () => {
          try {
            const text = await navigator.clipboard.readText();
            ws?.send(text);
          } catch {
            /* clipboard access denied */
          }
        },
        disabled: !isConnected,
      },
      {
        label: 'Clear',
        action: () => {
          term?.write('\x1b[2J\x1b[H');
        },
      },
      {
        label: 'Reset Terminal',
        danger: true,
        action: () => {
          term?.write('\x1bc');
        },
      },
    ];

    setMenuItems(items);
    setMenuPos({ visible: true, x: event.clientX, y: event.clientY });
  }, []);

  useEffect(() => {
    if (!ref.current) return;

    const term = new XTerm({
      convertEol: true,
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      theme: { background: '#0b0f1a', foreground: '#e2e8f0', cursor: '#818cf8' },
    });
    
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(ref.current);

    // Attach context menu handler to terminal element
    term.element!.addEventListener('contextmenu', handleContextMenu);

    const fitTerminal = () => {
      try {
        fit.fit();
        const dims = fit.proposeDimensions();
        if (dims && wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({
            type: 'resize',
            width: dims.cols,
            height: dims.rows
          }));
        }
      } catch {
        /* layout not ready */
      }
    };

    // Initial fit after DOM paint
    requestAnimationFrame(() => fitTerminal());
    termRef.current = term;

    // Build the WebSocket connection URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const token = storageGetSync('jarvis_api_key') || '';
    const wsUrl = `${protocol}//${host}/api/workspaces/${workspace.id}/terminal?token=${encodeURIComponent(token)}`;

    term.writeln('\x1b[90mConnecting to workspace sandbox interactive terminal...\x1b[0m');

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      term.writeln('\x1b[32mInteractive terminal session established.\x1b[0m\r\n');
      fitTerminal();
    };

    ws.onmessage = async (event) => {
      let data = event.data;
      if (data instanceof Blob) {
        data = await data.text();
      }
      try {
        const msg = JSON.parse(data);
        if (msg.type === 'stdout') {
          term.write(msg.data);
        }
      } catch {
        // Raw text message
        term.write(data);
      }
    };

    ws.onerror = () => {
      term.writeln('\r\n\x1b[31mTerminal WebSocket error occurred.\x1b[0m');
    };

    ws.onclose = (event) => {
      term.writeln(`\r\n\x1b[31mTerminal session closed (code: ${event.code}, reason: ${event.reason || 'none'}).\x1b[0m`);
    };

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(data);
      }
    });

    const onResize = () => fitTerminal();

    window.addEventListener('resize', onResize);
    const ro = new ResizeObserver(onResize);
    if (ref.current) ro.observe(ref.current);

    return () => {
      mountedRef.current = false;
      term.element!.removeEventListener('contextmenu', handleContextMenu);
      window.removeEventListener('resize', onResize);
      ro.disconnect();
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
      term.dispose();
    };
  }, [workspace.id, handleContextMenu]);

  return (
    <div 
      className="h-full w-full bg-[#0b0f1a] p-1" 
      ref={ref}
    >
      {/* Context Menu Overlay */}
      {menuPos.visible && (
        <div
          ref={menuRef}
          className="fixed z-50 min-w-[160px] bg-[#1e293b] border border-[#334155] rounded shadow-lg py-1"
          style={{ left: menuPos.x, top: menuPos.y }}
          onClick={(e) => e.stopPropagation()}
        >
          {menuItems.map((item, idx) => (
            <button
              key={idx}
              className={`
                w-full text-left px-4 py-2 text-sm transition-colors
                ${item.disabled ? 'opacity-40 cursor-not-allowed' : 'hover:bg-[#334155] cursor-pointer'}
                ${item.danger ? 'text-[#f87171]' : 'text-[#e2e8f0]'}
              `}
              onClick={() => {
                item.action();
                setMenuPos(prev => ({ ...prev, visible: false }));
              }}
              disabled={item.disabled}
            >
              <div className="flex items-center justify-between">
                <span>{item.label}</span>
                {item.shortcut && (
                  <span className="text-[#94a3b8] text-xs ml-4">{item.shortcut}</span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
