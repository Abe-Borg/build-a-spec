import { useEffect, useRef } from "react";
import type { RefObject } from "react";
import { dialogStack } from "./dialogStack.ts";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter(
    (element) =>
      !element.hasAttribute("disabled") &&
      element.getAttribute("aria-hidden") !== "true" &&
      element.getClientRects().length > 0,
  );
}

/**
 * Practical modal keyboard behavior shared by every dialog in the app: initial
 * focus, Escape, Tab containment, and focus restoration.
 *
 * ONE Escape mechanism. Every dialog listens on `document`, calls
 * `preventDefault()` before closing (the "already handled" signal the guided
 * tour's window listener reads), and enters the shared `dialogStack` — only
 * the topmost dialog acts on Escape or Tab, so a dialog stacked over another
 * closes alone whichever listener happened to register first.
 */
export function useDialogFocus(
  open: boolean,
  containerRef: RefObject<HTMLElement>,
  initialFocusRef: RefObject<HTMLElement>,
  onClose: () => void,
  restoreFallbackRef?: RefObject<HTMLElement>,
): void {
  const token = useRef<object>({}).current;
  const latestClose = useRef(onClose);
  useEffect(() => {
    latestClose.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const focusFrame = window.requestAnimationFrame(() => {
      const container = containerRef.current;
      if (!container) return;
      const target =
        initialFocusRef.current ?? focusableElements(container)[0] ?? container;
      target.focus();
    });

    const onKeyDown = (event: KeyboardEvent) => {
      // A dialog above this one already took the key, or is the one that
      // should: both are "not ours".
      if (event.defaultPrevented || !dialogStack.isTop(token)) return;
      const container = containerRef.current;
      if (!container) return;
      if (event.key === "Escape") {
        event.preventDefault();
        latestClose.current();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = focusableElements(container);
      if (focusable.length === 0) {
        event.preventDefault();
        container.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      const activeIndex = focusable.indexOf(active as HTMLElement);
      if (event.shiftKey) {
        if (activeIndex <= 0) {
          event.preventDefault();
          last.focus();
        }
      } else if (activeIndex === -1 || active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    // Enter the stack in the SAME effect that registers the listener, so
    // stack order is listener order, and leave it in the same cleanup.
    const leave = dialogStack.enter(token);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      leave();
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", onKeyDown);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
      else restoreFallbackRef?.current?.focus();
    };
  }, [open, containerRef, initialFocusRef, restoreFallbackRef, token]);
}
