import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import SharedWithYouCard from './SharedWithYouCard';
import type { HealthFamilyPerson } from '../../types/api';

vi.mock('../chat/familyDirectory', () => ({
  useFamilyDirectory: () => (id?: string | null) => ({ username: id ?? '', display_name: 'Jeremiah' }),
}));

function person(overrides: Partial<HealthFamilyPerson> = {}): HealthFamilyPerson {
  return { user_id: 'jeremiah', scopes: ['digital'], ...overrides };
}

describe('SharedWithYouCard digital scope', () => {
  it('shows the shared screen time for the day', () => {
    render(<SharedWithYouCard people={[person({ digital: { shared: true, available: true, screen_time_min: 60, day: '2026-10-25' } })]} />);
    expect(screen.getByTestId('shared-digital-jeremiah')).toHaveTextContent('1 h 0 min on the phone today');
  });

  it('says when the phone reported nothing, never a zero', () => {
    render(<SharedWithYouCard people={[person({ digital: { shared: true, available: true, screen_time_min: null, day: '2026-10-25' } })]} />);
    expect(screen.getByTestId('shared-digital-jeremiah')).toHaveTextContent('No phone use reported today');
  });

  it('says when the number could not be read', () => {
    render(<SharedWithYouCard people={[person({ digital: { shared: true, available: false, screen_time_min: null, day: '2026-10-25' } })]} />);
    expect(screen.getByTestId('shared-digital-jeremiah')).toHaveTextContent('Phone use not available right now');
  });
});
