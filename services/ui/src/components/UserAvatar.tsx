import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';
import { avatarTint, initials } from './chat/chatModel';
import { presetFor, presetSvgHtml } from '../lib/avatarPresets';

interface UserAvatarProps {
  username: string;
  /** The user's ``avatar`` value: "preset:<id>", "upload:<version>" or empty. */
  avatar?: string | null;
  /** Shown as initials when there is no picture. */
  name?: string;
  size?: number;
  className?: string;
}

/**
 * One way to draw a person everywhere: their built-in picture, their uploaded
 * photo, or their initials on their own colour. An uploaded photo is fetched
 * with the user's credentials (an <img> tag cannot send them) and cached per
 * version, so a new upload replaces it everywhere at once.
 */
export default function UserAvatar({ username, avatar, name, size = 40, className = '' }: UserAvatarProps) {
  const preset = presetFor(avatar);
  const uploaded = avatar?.startsWith('upload:') ? avatar : null;
  const { data: blob } = useQuery({
    queryKey: ['avatar-image', username, uploaded],
    queryFn: () => api.getAvatarImage(username),
    enabled: Boolean(uploaded),
    staleTime: Infinity,
    retry: false,
  });
  const src = useMemo(() => (blob ? URL.createObjectURL(blob) : null), [blob]);
  useEffect(() => () => {
    if (src) URL.revokeObjectURL(src);
  }, [src]);

  const box = { width: size, height: size };
  const label = name || username;
  const base = `relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full select-none ${className}`;

  if (uploaded && src) {
    return <img src={src} alt={label} style={box} className={`${base} object-cover`} data-testid="user-avatar-photo" />;
  }
  if (preset) {
    return (
      <span
        role="img"
        aria-label={`${label}: ${preset.name}`}
        title={preset.name}
        style={{ ...box, background: `linear-gradient(135deg, ${preset.from}, ${preset.to})` }}
        className={base}
        data-testid="user-avatar-preset"
        dangerouslySetInnerHTML={{ __html: presetSvgHtml(preset) }}
      />
    );
  }
  // White, not text-white: light themes remap text-white to dark ink, and the
  // tint behind the initials is always dark.
  return (
    <span
      role="img"
      aria-label={label}
      style={{ ...box, background: avatarTint(label), fontSize: size * 0.38 }}
      className={`${base} font-semibold text-[#fff]`}
      data-testid="user-avatar-initials"
    >
      {initials(label)}
    </span>
  );
}
