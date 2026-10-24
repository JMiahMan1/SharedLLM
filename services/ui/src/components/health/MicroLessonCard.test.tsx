import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import MicroLessonCard from './MicroLessonCard';
import { LESSONS, lessonIndexFor } from '../../lib/healthLessons';

vi.mock('../../lib/healthHub', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/healthHub')>()),
  localDay: () => '2026-10-24',
}));

describe('micro-lesson library', () => {
  it('has unique ids, every topic, and nothing about calories, weight or dieting', () => {
    expect(new Set(LESSONS.map((lesson) => lesson.id)).size).toBe(LESSONS.length);
    expect(new Set(LESSONS.map((lesson) => lesson.topic))).toEqual(new Set(['nutrition', 'sleep', 'movement', 'stress', 'water']));
    for (const lesson of LESSONS) {
      const text = `${lesson.title} ${lesson.body} ${lesson.tryToday}`.toLowerCase();
      expect(text).not.toMatch(/calorie|kcal|weight|diet|lose|fat\b|junk|bad food/);
      expect(lesson.tryToday.length).toBeGreaterThan(10);
    }
  });

  it('picks the same card for everyone on a day and moves on the next', () => {
    expect(lessonIndexFor('2026-10-24')).toBe(lessonIndexFor('2026-10-24'));
    expect(lessonIndexFor('2026-10-25')).toBe((lessonIndexFor('2026-10-24') + 1) % LESSONS.length);
  });
});

describe('MicroLessonCard', () => {
  it('shows one card and steps through the library', () => {
    render(<MicroLessonCard />);
    const first = LESSONS[lessonIndexFor('2026-10-24')];
    expect(screen.getByTestId('lesson-title')).toHaveTextContent(first.title);
    fireEvent.click(screen.getByRole('button', { name: 'Next lesson' }));
    const next = LESSONS[(lessonIndexFor('2026-10-24') + 1) % LESSONS.length];
    expect(screen.getByTestId('lesson-title')).toHaveTextContent(next.title);
    fireEvent.click(screen.getByText("Back to today's"));
    expect(screen.getByTestId('lesson-title')).toHaveTextContent(first.title);
  });
});
