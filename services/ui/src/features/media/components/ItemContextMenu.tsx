import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Disc3, Heart, ListEnd, ListStart, MoreHorizontal, User } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../../services/api';
import { errorText } from '../../../lib/errorText';
import { useMediaScreen } from '../pages/MediaScreen';
import { canQueue, detailPath, playRequestFor, type ItemLike } from '../items';

export interface MenuItemSource extends ItemLike {
  artist_uri?: string;
  album_uri?: string;
  favorite?: boolean | null;
}

/**
 * The "…" menu on every row and card (P5-T7): Play next and Add to queue on
 * whichever output is selected (this device, a Music Assistant speaker or a
 * Home Assistant speaker, through the same routing as Play), Go to artist and
 * album, and Favorite. A real menu: arrow keys move, Escape closes and puts
 * focus back on the button.
 */
export function ItemContextMenu({ item }: { item: MenuItemSource }) {
  const { playback } = useMediaScreen();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [favorite, setFavorite] = useState<boolean | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const req = playRequestFor(item);
  const artistPath = item.artist_uri ? detailPath({ uri: item.artist_uri, media_type: 'artist' }) : null;
  const albumPath = item.album_uri ? detailPath({ uri: item.album_uri, media_type: 'album' }) : null;
  // Favourites live in Music Assistant; Audiobookshelf items have none.
  const canFavorite = Boolean(item.uri) && !item.uri.startsWith('abs://');
  const isFavorite = favorite ?? Boolean(item.favorite);

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onPointer = (e: PointerEvent) => {
      if (!menuRef.current?.contains(e.target as Node) && !buttonRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    return () => document.removeEventListener('pointerdown', onPointer);
  }, [open]);

  const close = () => {
    setOpen(false);
    buttonRef.current?.focus();
  };

  const onMenuKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      items[(i + 1) % items.length]?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      items[(i - 1 + items.length) % items.length]?.focus();
    } else if (e.key === 'Home') {
      e.preventDefault();
      items[0]?.focus();
    } else if (e.key === 'End') {
      e.preventDefault();
      items.at(-1)?.focus();
    } else if (e.key === 'Tab') {
      setOpen(false);
    }
  };

  const toggleFavorite = async () => {
    close();
    const next = !isFavorite;
    setFavorite(next);
    try {
      await api.setMediaFavorite(item.uri, next);
      toast.success(next ? `${item.name} added to favorites` : `${item.name} removed from favorites`);
    } catch (err) {
      setFavorite(!next);
      toast.error(errorText(err, 'Could not update favorites'));
    }
  };

  type Action = 'next' | 'add' | 'artist' | 'album' | 'favorite';
  const select = (action: Action) => {
    if (action === 'next' || action === 'add') {
      close();
      if (req) void playback.enqueue(req, action);
    } else if (action === 'artist' || action === 'album') {
      setOpen(false);
      const path = action === 'artist' ? artistPath : albumPath;
      if (path) navigate(path);
    } else {
      void toggleFavorite();
    }
  };

  const queueable = Boolean(req) && canQueue(item);
  const entries: Array<{ action: Action; label: string; icon: ReactNode }> = [
    ...(queueable ? [
      { action: 'next' as const, label: 'Play next', icon: <ListStart size={16} /> },
      { action: 'add' as const, label: 'Add to queue', icon: <ListEnd size={16} /> },
    ] : []),
    ...(artistPath ? [{ action: 'artist' as const, label: 'Go to artist', icon: <User size={16} /> }] : []),
    ...(albumPath ? [{ action: 'album' as const, label: 'Go to album', icon: <Disc3 size={16} /> }] : []),
    ...(canFavorite ? [{
      action: 'favorite' as const,
      label: isFavorite ? 'Remove from favorites' : 'Add to favorites',
      icon: <Heart size={16} fill={isFavorite ? 'currentColor' : 'none'} />,
    }] : []),
  ];
  if (entries.length === 0) return null;

  return (
    <div className="relative shrink-0">
      <button
        ref={buttonRef}
        type="button"
        aria-label={`More for ${item.name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-[var(--site-text-muted)] hover:bg-[var(--site-card-bg-hover)] hover:text-[var(--site-text)]"
      >
        <MoreHorizontal size={18} aria-hidden />
      </button>
      {open ? (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={`Actions for ${item.name}`}
          onKeyDown={onMenuKey}
          className="absolute right-0 top-full z-50 mt-1 w-56 rounded-xl border border-[var(--color-border-mid)] bg-[var(--site-menu-a)] p-1 shadow-2xl"
        >
          {entries.map((e) => (
            <button
              key={e.action}
              type="button"
              role="menuitem"
              tabIndex={-1}
              onClick={() => select(e.action)}
              className="flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm text-[var(--site-text)] hover:bg-[var(--site-card-bg-hover)] focus:bg-[var(--site-card-bg-hover)] focus:outline-none"
            >
              <span className="text-[var(--site-text-muted)]" aria-hidden>{e.icon}</span>
              {e.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
