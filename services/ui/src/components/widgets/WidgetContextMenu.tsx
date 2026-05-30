import { useRef, useEffect, useState, useCallback } from 'react';
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

const WidgetContextMenu = (props: WidgetContextMenuProps) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<ContextMenuPosition>({ x: 0, y: 0 });
  const menuRef = useRef<HTMLDivElement>(null);

  const handleRightClick = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setMenuPos({ x: e.clientX, y: e.clientY });
    setMenuOpen(true);
  }, []);

  const handleLongPressStart = useCallback((e: React.TouchEvent) => {
    const touch = e.touches[0];
    const pressTimer = setTimeout(() => {
      setMenuPos({ x: touch.clientX, y: touch.clientY });
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
  }, []);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };

    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setMenuOpen(false);
      }
    };

    if (menuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleEsc);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [menuOpen]);

  if (!menuOpen) {
    return (
      <div
        onContextMenu={handleRightClick}
        onTouchStart={handleLongPressStart}
        className="absolute top-2 right-2 z-10"
      >
        <button
          onClick={(e) => {
            e.stopPropagation();
            setMenuPos({ x: e.clientX || 0, y: e.clientY || 0 });
            setMenuOpen(true);
          }}
          className="text-slate-500 hover:text-white transition-colors p-1"
          title="Widget options"
        >
          ⚙
        </button>
      </div>
    );
  }

  const clampPosition = (pos: ContextMenuPosition): ContextMenuPosition => ({
    x: Math.min(pos.x, window.innerWidth - 200),
    y: Math.min(pos.y, window.innerHeight - 300),
  });

  const displayPos = clampPosition(menuPos);

  return (
    <>
      <div
        className="fixed inset-0 z-40"
        onClick={() => setMenuOpen(false)}
      />
      <div
        ref={menuRef}
        style={{ top: displayPos.y, left: displayPos.x }}
        className="fixed z-50 glass-card min-w-[180px] p-2 animate-in fade-in"
      >
        <div className="space-y-1">
          <div className="text-xs font-semibold text-white px-2 py-1 mb-1">
            {props.def.label}
          </div>

          <button
            onClick={() => {
              props.onTogglePin(props.widgetKey);
              setMenuOpen(false);
            }}
            className="w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-slate-700/50 text-slate-300 hover:text-white transition-colors flex items-center gap-2"
          >
            {props.userSettings.is_pinned ? '📌 Unpin' : '📌 Pin'}
          </button>

          <div className="border-t border-slate-700/50 my-1" />

          <div className="px-2 py-1 text-xs text-slate-500">Size</div>
          {SIZE_OPTIONS.map((size) => (
            <button
              key={size.value}
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
              onClick={() => {
                props.onToggleVisibility(props.widgetKey, !props.userSettings.is_pinned);
                setMenuOpen(false);
              }}
              className="w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-slate-700/50 text-slate-300 hover:text-white transition-colors"
            >
              {props.userSettings.visibility === 'hidden' ? '👁 Show' : '🙈 Hide'}
            </button>
          )}

          <button
            onClick={() => {
              props.onReorder(props.widgetKey, props.totalWidgets);
              setMenuOpen(false);
            }}
            className="w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-slate-700/50 text-slate-300 hover:text-white transition-colors"
          >
            ⬇ Move to bottom
          </button>

          <div className="border-t border-slate-700/50 my-1" />

          <button
            onClick={() => {
              props.onRemove(props.widgetKey);
              setMenuOpen(false);
            }}
            className="w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-red-900/30 text-red-400 hover:text-red-300 transition-colors"
          >
            Remove
          </button>
        </div>
      </div>
    </>
  );
};

export default WidgetContextMenu;
