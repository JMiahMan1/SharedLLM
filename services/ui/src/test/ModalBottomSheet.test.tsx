import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Modal from '../components/ui/Modal';

describe('Modal', () => {
  it('renders nothing when closed', () => {
    render(
      <Modal isOpen={false} onClose={vi.fn()} title="Edit trip">
        body
      </Modal>,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('is labelled and modal for assistive tech', () => {
    render(
      <Modal isOpen onClose={vi.fn()} title="Edit trip">
        body
      </Modal>,
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAttribute('aria-label', 'Edit trip');
  });

  it('opens as a bottom sheet on phones and a centred dialog from sm up', () => {
    render(
      <Modal isOpen onClose={vi.fn()} title="Edit trip">
        body
      </Modal>,
    );
    // `items-end` + `p-0` at the base, `sm:items-center sm:justify-center sm:p-4`
    // for the desktop switch the app uses at 768px.
    const dialog = screen.getByRole('dialog');
    expect(dialog.className).toContain('rounded-t-2xl');
    expect(dialog.className).toContain('sm:rounded-2xl');
    expect(dialog.className).toContain('safe-area-bottom');
  });

  it('closes on Escape', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Modal isOpen onClose={onClose} title="Edit trip">
        body
      </Modal>,
    );
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('locks page scroll while open and restores it after', () => {
    const { unmount } = render(
      <Modal isOpen onClose={vi.fn()} title="Edit trip">
        body
      </Modal>,
    );
    expect(document.body.style.overflow).toBe('hidden');
    unmount();
    expect(document.body.style.overflow).not.toBe('hidden');
  });

  it('closes from the labelled close button', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Modal isOpen onClose={onClose} title="Edit trip">
        body
      </Modal>,
    );
    await user.click(screen.getByRole('button', { name: /close dialog/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it('gives the close button a touch-sized target', () => {
    render(
      <Modal isOpen onClose={vi.fn()} title="Edit trip">
        body
      </Modal>,
    );
    const close = screen.getByRole('button', { name: /close dialog/i });
    expect(close.className).toContain('pointer-coarse:h-11');
    expect(close.className).toContain('pointer-coarse:w-11');
  });
});