import { useRef, useEffect, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import {
  Settings2,
  Pin,
  PinOff,
  Eye,
  EyeOff,
  ChevronUp,
  ChevronDown,
  ArrowDownToLine,
  Trash2,
} from 'lucide-react';
import type { WidgetSize, WidgetContextMenuProps } from '../../types/widget';

const SIZE_OPTIONS: { value: WidgetSize; label: string }[] = [
  { value: 'small', label: 'Small' },
  { value: 'medium', label: 'Medium' },
  { value: 'wide', label: 'Wide' },
  { value: 'tall', label: 'Tall' },
];

interface ContextMenuPosition {
  x: number;
  y: number;
}

/** Every focusable action inside the menu, for roving keyboard navigation. */
const MENU_ITEM_SELECTOR = '[role="menuitem"],[role="menuitemradio"]';

const WidgetContextMenu = (props: WidgetContextMenuProps) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<ContextMenuPosition>({ x: 0, y: 0 });
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLDivElement>(null);

  const containerClassName = props.className !== undefined ? props.className : "relative z-20";

  const getButtonPosition = useCallback((): ContextMenuPosition => {
    const el = triggerRef.current;
    if (el) {
      const rect = el.getBoundingClientRect();
      return { x: rect.right, y: rect.bottom };
    }
    return { x: window.innerWidth - 200, y: window.innerHeight - 300 };
  }, []);

  const handleRightClick = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setMenuPos(getButtonPosition());
    setMenuOpen(true);
  }, [getButtonPosition]);

  const handleLongPressStart = useCallback((e: React.TouchEvent) => {
    void e;
    const pressTimer = setTimeout(() => {
      setMenuPos(getButtonPosition());
      setMenuOpen(true);
    }, 500);

    const handleMove = () => {
      clearTimeout(pressTimer);
      document.removeEventListener('touchmove', handleMove);
      document.removeEventListener('touchend', handleLongPressEnd);
    };

    const handleLongPressEnd = () => {
      clearTimeout(pressTimer);
      document.removeEventListener('touchmove', handleMove);
      document.removeEventListener('touchend', handleLongPressEnd);
    };

    document.addEventListener('touchmove', handleMove, { passive: true });
    document.addEventListener('touchend', handleLongPressEnd);
  }, [getButtonPosition]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };

    const handleKeys = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setMenuOpen(false);
        return;
      }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;

      const items = Array.from(
        menuRef.current?.querySelectorAll<HTMLButtonElement>(MENU_ITEM_SELECTOR + ':not([disabled])') ?? []
      );
      if (items.length === 0) return;

      e.preventDefault();
      const current = items.indexOf(document.activeElement as HTMLButtonElement);
      const step = e.key === 'ArrowDown' ? 1 : -1;
      const next = current === -1 ? 0 : (current + step + items.length) % items.length;
      items[next]?.focus();
    };

    if (menuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleKeys);
      // Land keyboard users on the first action instead of stranding focus.
      menuRef.current
        ?.querySelector<HTMLButtonElement>(MENU_ITEM_SELECTOR + ':not([disabled])')
        ?.focus();
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeys);
    };
  }, [menuOpen]);

  const triggerButton = (
    <div
      ref={triggerRef}
      onContextMenu={handleRightClick}
      onTouchStart={handleLongPressStart}
      className={containerClassName}
    >
      <button
        onClick={(e) => {
          e.stopPropagation();
          const rect = e.currentTarget.getBoundingClientRect();
          setMenuPos({ x: rect.right, y: rect.bottom });
          setMenuOpen(true);
        }}
        className="text-slate-500 hover:text-white transition-colors p-1 rounded hover:bg-white/5"
        title="Widget options"
        aria-label="Widget options"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
      >
        <Settings2 size={14} />
      </button>
    </div>
  );

  if (!menuOpen) {
    return triggerButton;
  }

  const clampPosition = (pos: ContextMenuPosition): ContextMenuPosition => ({
    x: Math.max(0, Math.min(pos.x, window.innerWidth - 200)),
    y: Math.max(0, Math.min(pos.y, window.innerHeight - 300)),
  });

  const displayPos = clampPosition(menuPos);

  const menuContent = (
    <>
      <div
        className="fixed inset-0 z-40 bg-slate-950/30 backdrop-blur-sm"
        onClick={() => setMenuOpen(false)}
      />
      <div
        ref={menuRef}
        style={{ top: displayPos.y, left: displayPos.x }}
        className="fixed z-50 glass-card min-w-[180px] p-2 animate-in fade-in"
        role="menu"
        aria-label={`${props.def.label} options`}
      >
        <div className="space-y-1">
          <div className="text-xs font-semibold text-white px-2 py-1 mb-1">
            {props.def.label}
          </div>

          <button
            role="menuitem"
            onClick={() => {
              props.onTogglePin(props.widgetKey);
              setMenuOpen(false);
            }}
            className="w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-slate-700/50 text-slate-300 hover:text-white transition-colors flex items-center gap-2"
          >
            {props.userSettings.is_pinned ? <PinOff size={13} /> : <Pin size={13} />}
            {props.userSettings.is_pinned ? 'Unpin' : 'Pin to top'}
          </button>

          <div className="border-t border-slate-700/50 my-1" />

          <div className="px-2 py-1 text-xs text-slate-500">Size</div>
          {SIZE_OPTIONS.map((size) => (
            <button
              key={size.value}
              role="menuitemradio"
              aria-checked={props.userSettings.size === size.value}
              onClick={() => {
                props.onResize(props.widgetKey, size.value);
                setMenuOpen(false);
              }}
              className={`w-full text-left text-xs px-2 py-1.5 rounded-md transition-colors ${
                props.userSettings.size === size.value
                  ? 'bg-indigo-600/30 text-white'
                  : 'text-slate-400 hover:text-white hover:bg-slate-700/50'
              }`}
            >
              {props.userSettings.size === size.value && '✓ '}{size.label}
            </button>
          ))}

          <div className="border-t border-slate-700/50 my-1" />

          {props.widgetKey !== 'active_media' && (
            <button
              role="menuitem"
              onClick={() => {
                props.onToggleVisibility(props.widgetKey, props.userSettings.visibility !== 'visible');
                setMenuOpen(false);
              }}
              className="w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-slate-700/50 text-slate-300 hover:text-white transition-colors flex items-center gap-2"
            >
              {props.userSettings.visibility === 'hidden' ? <Eye size={13} /> : <EyeOff size={13} />}
              {props.userSettings.visibility === 'hidden' ? 'Show' : 'Hide'}
            </button>
          )}

          <button
            role="menuitem"
            disabled={props.currentIndex <= 0}
            onClick={() => {
              props.onReorder(props.widgetKey, props.currentIndex - 1);
              setMenuOpen(false);
            }}
            className="w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-slate-700/50 text-slate-300 hover:text-white transition-colors disabled:opacity-40 disabled:hover:bg-transparent flex items-center gap-2"
          >
            <ChevronUp size={13} />
            Move up
          </button>

          <button
            role="menuitem"
            disabled={props.currentIndex >= props.totalWidgets - 1}
            onClick={() => {
              props.onReorder(props.widgetKey, props.currentIndex + 1);
              setMenuOpen(false);
            }}
            className="w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-slate-700/50 text-slate-300 hover:text-white transition-colors disabled:opacity-40 disabled:hover:bg-transparent flex items-center gap-2"
          >
            <ChevronDown size={13} />
            Move down
          </button>

          <button
            role="menuitem"
            disabled={props.currentIndex >= props.totalWidgets - 1}
            onClick={() => {
              props.onReorder(props.widgetKey, props.totalWidgets - 1);
              setMenuOpen(false);
            }}
            className="w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-slate-700/50 text-slate-300 hover:text-white transition-colors disabled:opacity-40 disabled:hover:bg-transparent flex items-center gap-2"
          >
            <ArrowDownToLine size={13} />
            Move to bottom
          </button>

          <div className="border-t border-slate-700/50 my-1" />

          <button
            role="menuitem"
            onClick={() => {
              props.onRemove(props.widgetKey);
              setMenuOpen(false);
            }}
            className="w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-red-900/30 text-red-400 hover:text-red-300 transition-colors flex items-center gap-2"
          >
            <Trash2 size={13} />
            Remove
          </button>
        </div>
      </div>
    </>
  );

  return (
    <>
      {triggerButton}
      {createPortal(menuContent, document.body)}
    </>
  );
};

export default WidgetContextMenu;
