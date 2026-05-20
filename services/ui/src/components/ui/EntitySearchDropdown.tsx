import { useState, useRef, useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search, X } from 'lucide-react';
import { api } from '../services/api';

interface EntitySearchDropdownProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  domainFilter?: string;
  className?: string;
}

export default function EntitySearchDropdown({
  value,
  onChange,
  placeholder = 'Search entities...',
  domainFilter,
  className = '',
}: EntitySearchDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const { data: entities = [] } = useQuery({
    queryKey: ['ha-entities'],
    queryFn: () => api.getEntities(),
    staleTime: 60_000,
  });

  const filtered = useMemo(() => {
    let list = entities;
    if (domainFilter) {
      list = list.filter((e) => e.domain === domainFilter);
    }
    if (!search.trim()) return list.slice(0, 50);
    const q = search.toLowerCase();
    return list.filter(
      (e) =>
        e.entity_id.toLowerCase().includes(q) ||
        e.friendly_name.toLowerCase().includes(q) ||
        e.domain.toLowerCase().includes(q),
    ).slice(0, 50);
  }, [entities, search, domainFilter]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  const handleSelect = (entityId: string) => {
    onChange(entityId);
    setSearch('');
    setIsOpen(false);
  };

  const handleClear = () => {
    onChange('');
    setSearch('');
  };

  const displayValue = value
    ? entities.find((e) => e.entity_id === value)?.friendly_name || value
    : '';

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <div className="relative">
        <Search
          size={16}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none"
        />
        <input
          ref={inputRef}
          type="text"
          value={isOpen ? search : displayValue}
          onChange={(e) => {
            setSearch(e.target.value);
            setIsOpen(true);
          }}
          onFocus={() => {
            setSearch('');
            setIsOpen(true);
          }}
          placeholder={placeholder}
          className="glass-input w-full pl-9 pr-8"
        />
        {value && !isOpen && (
          <button
            type="button"
            onClick={handleClear}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white"
          >
            <X size={14} />
          </button>
        )}
      </div>

      {isOpen && (
        <div className="absolute z-50 mt-1 w-full max-h-64 overflow-auto rounded-xl border border-white/10 bg-slate-900/95 backdrop-blur-xl shadow-2xl">
          {filtered.length === 0 ? (
            <div className="px-4 py-3 text-sm text-slate-500">No entities found</div>
          ) : (
            <ul className="py-1">
              {filtered.map((entity) => (
                <li key={entity.entity_id}>
                  <button
                    type="button"
                    onClick={() => handleSelect(entity.entity_id)}
                    className={`w-full px-4 py-2 text-left transition hover:bg-white/10 ${
                      entity.entity_id === value ? 'bg-white/10' : ''
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-indigo-400/70 w-24 truncate">
                        {entity.domain}
                      </span>
                      <span className="text-sm text-white truncate flex-1">
                        {entity.friendly_name || entity.entity_id}
                      </span>
                      <span className="text-xs text-slate-500 truncate max-w-[120px]">
                        {entity.entity_id}
                      </span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
