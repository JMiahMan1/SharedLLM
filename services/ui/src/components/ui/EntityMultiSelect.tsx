import { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search, X, Plus } from 'lucide-react';
import { api } from '../../services/api';
import { createPortal } from 'react-dom';

interface EntityMultiSelectProps {
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  domainFilter?: string;
  className?: string;
}

export default function EntityMultiSelect({
  values,
  onChange,
  placeholder = 'Search and add entities...',
  domainFilter,
  className = '',
}: EntityMultiSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0, width: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const { data: entities = [] } = useQuery({
    queryKey: ['ha-entities'],
    queryFn: () => api.getEntities(),
    staleTime: 60_000,
  });

  const filtered = useMemo(() => {
    let list = entities.filter((e) => !values.includes(e.entity_id));
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
  }, [entities, search, domainFilter, values]);

  const updateDropdownPosition = useCallback(() => {
    if (inputRef.current) {
      const rect = inputRef.current.getBoundingClientRect();
      setDropdownPos({
        top: rect.bottom + 4,
        left: rect.left,
        width: rect.width,
      });
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      updateDropdownPosition();
      const handleResize = () => updateDropdownPosition();
      window.addEventListener('resize', handleResize);
      window.addEventListener('scroll', handleResize, true);
      return () => {
        window.removeEventListener('resize', handleResize);
        window.removeEventListener('scroll', handleResize, true);
      };
    }
  }, [isOpen, updateDropdownPosition]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      // Don't close if clicking inside the portal dropdown
      const dropdownEl = document.querySelector('.entity-dropdown-portal');
      if (dropdownEl && dropdownEl.contains(target)) return;
      if (containerRef.current && !containerRef.current.contains(target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleAdd = (entityId: string) => {
    if (!values.includes(entityId)) {
      onChange([...values, entityId]);
    }
    setSearch('');
    inputRef.current?.focus();
  };

  const handleRemove = (entityId: string) => {
    onChange(values.filter((v) => v !== entityId));
  };

  const selectedEntities = useMemo(
    () =>
      values
        .map((id) => entities.find((e) => e.entity_id === id))
        .filter(Boolean)
        .map((e) => e!),
    [values, entities],
  );

  const dropdown = isOpen ? (
    <div
      className="entity-dropdown-portal fixed z-[100] max-h-64 overflow-auto rounded-xl border border-white/10 bg-slate-900/95 backdrop-blur-xl shadow-2xl"
      style={{ top: dropdownPos.top, left: dropdownPos.left, width: dropdownPos.width }}
    >
      {filtered.length === 0 ? (
        <div className="px-4 py-3 text-sm text-slate-500">
          {search ? 'No matching entities' : 'All entities already added'}
        </div>
      ) : (
        <ul className="py-1">
          {filtered.map((entity) => (
            <li key={entity.entity_id}>
              <button
                type="button"
                onClick={() => handleAdd(entity.entity_id)}
                className="w-full px-4 py-2 text-left transition hover:bg-white/10"
              >
                <div className="flex items-center gap-2">
                  <Plus size={12} className="text-emerald-400" />
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
  ) : null;

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      {/* Selected tags */}
      {selectedEntities.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {selectedEntities.map((entity) => (
            <span
              key={entity.entity_id}
              className="inline-flex items-center gap-1 rounded-lg bg-indigo-500/20 px-2 py-1 text-xs text-indigo-300"
            >
              <span className="max-w-[200px] truncate">
                {entity.friendly_name || entity.entity_id}
              </span>
              <button
                type="button"
                onClick={() => handleRemove(entity.entity_id)}
                className="ml-0.5 text-indigo-400 hover:text-white"
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Search input */}
      <div className="relative">
        <Search
          size={16}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none"
        />
        <input
          ref={inputRef}
          type="text"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setIsOpen(true);
          }}
          onFocus={() => setIsOpen(true)}
          placeholder={values.length === 0 ? placeholder : 'Add more entities...'}
          className="glass-input w-full pl-9"
        />
      </div>

      {/* Dropdown rendered via portal to avoid overflow clipping */}
      {typeof document !== 'undefined' && dropdown && createPortal(dropdown, document.body)}
    </div>
  );
}
