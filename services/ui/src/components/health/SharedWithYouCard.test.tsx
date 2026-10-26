import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
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
    expect(screen.getByTestId('shared-digital-jeremiah')).toHaveTextContent('1h on the phone today');
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

describe('SharedWithYouCard failure', () => {
  it('says the read failed instead of rendering nothing at all', () => {
    const onRetry = vi.fn();
    render(
      <SharedWithYouCard
        people={[]}
        error="Could not load what your family shared"
        onRetry={onRetry}
      />,
    );
    expect(screen.getByTestId('health-family-error')).toHaveTextContent(
      'Could not load what your family shared',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalled();
  });

  it('still renders nothing when there is nothing shared and no failure', () => {
    const { container } = render(<SharedWithYouCard people={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
