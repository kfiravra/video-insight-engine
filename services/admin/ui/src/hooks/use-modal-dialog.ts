import { useCallback, useEffect, useRef } from 'react';
import type { MouseEvent, RefObject } from 'react';

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Keep Tab and Shift+Tab inside the panel instead of letting focus walk onto the page behind. */
function trapTab(event: KeyboardEvent, panel: HTMLElement): void {
  const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
  if (items.length === 0) {
    event.preventDefault();
    panel.focus();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (!panel.contains(active)) {
    event.preventDefault();
    first.focus();
  } else if (event.shiftKey && (active === first || active === panel)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

interface ModalDialogOptions {
  onClose: () => void;
  /** While true (a request is in flight) Escape, the backdrop and `requestClose` do nothing. */
  locked: boolean;
}

interface BackdropHandlers {
  onMouseDown: (event: MouseEvent<HTMLElement>) => void;
  onClick: (event: MouseEvent<HTMLElement>) => void;
}

export interface ModalDialog {
  /** Put on the dialog panel: it takes focus on mount and is the Tab-trap boundary. */
  panelRef: RefObject<HTMLDivElement | null>;
  /** Spread on the backdrop: closes only when the press began on the backdrop too, so dragging a text selection out of the panel does not dismiss it. */
  backdropProps: BackdropHandlers;
  /** Close unless locked: for the Cancel button. */
  requestClose: () => void;
}

/**
 * Behaviour every modal needs: focus moves into the panel once when it opens
 * and returns to whatever opened it when it closes, Tab stays inside, Escape and
 * the backdrop close it, and none of them can while `locked`.
 *
 * `onClose` and `locked` are read through refs so a parent re-render (a new
 * closure on every render, a polled query refreshing) never re-runs the effect
 * and pulls focus back to the panel mid-typing.
 */
export function useModalDialog({ onClose, locked }: ModalDialogOptions): ModalDialog {
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const lockedRef = useRef(locked);
  const pressStartedOnBackdrop = useRef(false);

  useEffect(() => {
    onCloseRef.current = onClose;
    lockedRef.current = locked;
  });

  const requestClose = useCallback(() => {
    if (!lockedRef.current) onCloseRef.current();
  }, []);

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') requestClose();
      else if (event.key === 'Tab' && panelRef.current) trapTab(event, panelRef.current);
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      if (opener?.isConnected) opener.focus();
    };
  }, [requestClose]);

  const backdropProps: BackdropHandlers = {
    onMouseDown: (event) => {
      pressStartedOnBackdrop.current = event.target === event.currentTarget;
    },
    onClick: (event) => {
      const pressedAndReleasedOnBackdrop = pressStartedOnBackdrop.current && event.target === event.currentTarget;
      pressStartedOnBackdrop.current = false;
      if (pressedAndReleasedOnBackdrop) requestClose();
    },
  };

  return { panelRef, backdropProps, requestClose };
}
