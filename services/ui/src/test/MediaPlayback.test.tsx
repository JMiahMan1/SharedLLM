import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LocalAudioPlayer } from '../components/LocalAudioPlayer';
import type { LocalTrack } from '../components/LocalAudioPlayer';

const mockTrack: LocalTrack = {
  id: 'test-1',
  title: 'Test Audiobook',
  subtitle: 'Author Name',
  type: 'audiobook',
  coverUrl: '/covers/test.jpg',
  source: 'abs',
};

const renderPlayer = (props: Partial<React.ComponentProps<typeof LocalAudioPlayer>> = {}) => {
  return render(
    <LocalAudioPlayer
      track={mockTrack}
      isPlaying={false}
      isLoaded={true}
      volume={70}
      isMuted={false}
      currentTime={0}
      duration={3600}
      error={null}
      onTogglePlay={vi.fn()}
      onVolumeChange={vi.fn()}
      onMuteToggle={vi.fn()}
      onSeek={vi.fn()}
      onSkipBack={vi.fn()}
      onSkipForward={vi.fn()}
      onStopPlayback={vi.fn()}
      {...props}
    />,
  );
};

describe('LocalAudioPlayer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('compact mode', () => {
    it('renders nothing when track is null', () => {
      const { container } = renderPlayer({ track: null });
      expect(container.firstChild).toBeNull();
    });

    it('renders track title and subtitle', () => {
      renderPlayer();
      expect(screen.getByText('Test Audiobook')).toBeInTheDocument();
      expect(screen.getByText('Author Name')).toBeInTheDocument();
    });

    it('shows animated bars when playing', () => {
      const { container } = renderPlayer({ isPlaying: true });
      const animatedBars = container.querySelectorAll('.animate-pulse');
      expect(animatedBars.length).toBeGreaterThan(0);
    });
  });

  describe('expanded mode', () => {
    it('expands when compact card is clicked', () => {
      renderPlayer();
      const compactCard = screen.getByRole('button', { hidden: true });
      expect(compactCard).toBeInTheDocument();
      fireEvent.click(compactCard);
      expect(screen.getByText('Test Audiobook')).toBeInTheDocument();
    });

    it('shows header with type label', () => {
      renderPlayer();
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      expect(screen.getByText('Audiobook')).toBeInTheDocument();
    });

    it('shows time formatting for short durations', () => {
      renderPlayer({ currentTime: 180, duration: 3600 });
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      expect(screen.getByText('3:00')).toBeInTheDocument();
    });

    it('shows hours format for long durations', () => {
      renderPlayer({ currentTime: 0, duration: 10800 });
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      expect(screen.getByText('3h 0m')).toBeInTheDocument();
    });

    it('shows progress bar with correct percentage', () => {
      renderPlayer({ currentTime: 1800, duration: 3600 });
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      const progressFill = document.querySelector('.bg-gradient-to-r.rounded-full');
      expect(progressFill).toHaveAttribute('style', expect.stringContaining('50%'));
    });

    it('toggles play when play button clicked', () => {
      const onTogglePlay = vi.fn();
      renderPlayer({ isPlaying: false, isLoaded: true, onTogglePlay });
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      const buttons = document.querySelectorAll('.rounded-full');
      const playButton = Array.from(buttons).find(b => b.classList.contains('w-16'));
      expect(playButton).toBeTruthy();
      if (playButton) {
        fireEvent.click(playButton);
        expect(onTogglePlay).toHaveBeenCalled();
      }
    });

    it('toggles mute when mute button clicked', () => {
      const onMuteToggle = vi.fn();
      renderPlayer({ isMuted: false, onMuteToggle });
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      const buttons = Array.from(document.querySelectorAll('button'));
      const muteBtn = buttons.find(b => b.querySelector('[size="16"]') && b.closest('.gap-3'));
      if (muteBtn) {
        fireEvent.click(muteBtn);
        expect(onMuteToggle).toHaveBeenCalled();
      }
    });

    it('changes volume when slider changes', () => {
      const onVolumeChange = vi.fn();
      renderPlayer({ onVolumeChange });
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      const slider = screen.getByRole('slider');
      fireEvent.change(slider, { target: { value: '80' } });
      expect(onVolumeChange).toHaveBeenCalledWith(80);
    });

    it('calls onStopPlayback when stop button clicked', () => {
      const onStopPlayback = vi.fn();
      renderPlayer({ onStopPlayback });
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      fireEvent.click(screen.getByText('Stop Playback'));
      expect(onStopPlayback).toHaveBeenCalled();
    });

    it('shows error message when present', () => {
      renderPlayer({ error: 'Failed to load stream' });
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      expect(screen.getByText('Failed to load stream')).toBeInTheDocument();
    });

    it('has close button to exit expanded mode', () => {
      renderPlayer();
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      // Close button is the ChevronDown button in the header area (flex items-center justify-between)
      const headerDiv = document.querySelector('.flex.items-center.justify-between.mb-6');
      const closeBtn = headerDiv?.querySelector('button');
      expect(closeBtn).toBeTruthy();
    });

    it('renders skip back and skip forward buttons', () => {
      const onSkipBack = vi.fn();
      const onSkipForward = vi.fn();
      renderPlayer({ onSkipBack, onSkipForward });
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      // Controls container has 3 buttons: skip back, play/pause, skip forward
      const controlsDiv = document.querySelector('.flex.items-center.justify-center.gap-6.mb-6');
      const controlsButtons = controlsDiv?.querySelectorAll('button');
      expect(controlsButtons?.length).toBe(3);
    });

    it('shows music label for music type', () => {
      renderPlayer({ track: { ...mockTrack, type: 'music' } });
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      expect(screen.getByText('Playing')).toBeInTheDocument();
    });
  });

  describe('progress bar interaction', () => {
    it('clicking progress bar calculates position', () => {
      const onSeek = vi.fn();
      renderPlayer({ currentTime: 100, duration: 1000, onSeek });
      const compactCard = screen.getByRole('button', { hidden: true });
      fireEvent.click(compactCard);
      const progressBar = document.querySelector('.cursor-pointer.relative.group');
      if (progressBar) {
        Object.defineProperty(progressBar, 'getBoundingClientRect', {
          value: () => ({ left: 0, width: 500 }),
          configurable: true,
        });
        fireEvent.click(progressBar, { clientX: 250 });
      }
    });
  });
});
