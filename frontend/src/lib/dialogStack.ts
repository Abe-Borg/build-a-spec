/**
 * The open-dialog stack: which dialog owns the keyboard right now.
 *
 * Every dialog that uses `useDialogFocus` listens for Escape and Tab on
 * `document`, and document listeners fire in REGISTRATION order — so when a
 * dialog opens over another (Settings under Developer tools, Help under the
 * trust dossier, the tour's checkpoint under the end-tour confirmation), the
 * PARENT's listener runs first and, on its own, would close the parent while
 * the child is still up. Each hook enters this stack when it opens and asks
 * `isTop` before acting, so only the topmost dialog closes or contains focus —
 * independent of listener order and of React's synchronous flush.
 *
 * Pure and React-free so `node --test` can load it directly. `enter` returns
 * the leave function; leaving removes THAT token wherever it sits, never a
 * blind pop, so an out-of-order unmount cannot remove someone else's dialog.
 */
export interface DialogStack {
  /** Push a dialog; the returned function removes it (idempotent). */
  enter(token: object): () => void;
  /** True when this dialog is the one on top. */
  isTop(token: object): boolean;
  size(): number;
}

export function createDialogStack(): DialogStack {
  const stack: object[] = [];
  return {
    enter(token) {
      stack.push(token);
      let left = false;
      return () => {
        if (left) return;
        left = true;
        const index = stack.lastIndexOf(token);
        if (index >= 0) stack.splice(index, 1);
      };
    },
    isTop(token) {
      return stack.length > 0 && stack[stack.length - 1] === token;
    },
    size() {
      return stack.length;
    },
  };
}

/** The app's one stack — every `useDialogFocus` shares it. */
export const dialogStack = createDialogStack();
