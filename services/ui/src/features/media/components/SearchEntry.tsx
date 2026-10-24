import { Link, useNavigate } from 'react-router-dom';
import { Search } from 'lucide-react';
import { iconButton, muted } from './ui';

/**
 * The way into search from a media page's header: a field on wider screens
 * (typing goes straight to the search page with the text), an icon on phones.
 * `/` and ⌘K/Ctrl+K reach it from the keyboard.
 */
export function SearchEntry() {
  const navigate = useNavigate();
  return (
    <>
      <div className="relative hidden w-72 md:block">
        <Search size={16} className={`pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 ${muted}`} aria-hidden />
        <input
          type="search"
          data-media-search
          placeholder="Search music and books"
          aria-label="Search music and books"
          onChange={(e) => navigate(`/media/search?q=${encodeURIComponent(e.target.value)}`)}
          className="glass-input h-11 w-full rounded-xl pl-9 text-sm"
        />
      </div>
      <Link to="/media/search" aria-label="Search music and books" className={`${iconButton} md:hidden`}>
        <Search size={20} aria-hidden />
      </Link>
    </>
  );
}
