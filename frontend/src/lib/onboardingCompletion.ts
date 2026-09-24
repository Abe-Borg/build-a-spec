/**
 * Whether this install has finished the guided tour — the answer the empty
 * chat's tutorial chip reads ("Take the full interactive tutorial again", no
 * pulse, once it has).
 *
 * Kept by the server (`GET` / `PUT /api/ui/onboarding`,
 * backend/onboarding_state.py), not in browser storage: pywebview runs the
 * packaged app's WebView in private mode on a fresh loopback port every
 * launch, so localStorage never survives a relaunch. The version compared
 * here is the frontend's; the server stores the integer it is given.
 *
 * The answer is `null` until the saved one has been read, and the chip
 * renders a neutral state for `null` (no pulse, its subtitle reserved but
 * blank) so neither a first-timer nor a returning user sees it change under
 * them. A read that fails is "not completed": the pulse is the safe error,
 * since an invitation too many costs nothing.
 *
 * The store is a factory so the rules run in tests without a network; the
 * app uses the one default instance below.
 */
import {
  getOnboardingCompletion,
  saveOnboardingCompletion,
} from "./api.ts";

/**
 * Bumped when the tour changes enough that someone who finished the old one
 * should be invited to take it again. Version 1 was the former shortened
 * tour; its completion lived only in browser storage.
 */
export const ONBOARDING_COMPLETION_VERSION = 2;

/** True / false once known; null while the saved answer is being read. */
export type OnboardingCompleted = boolean | null;

/** Strict: only the current version, as a real number, counts as finished. */
export function completedFromApi(payload: unknown): boolean {
  if (!payload || typeof payload !== "object") return false;
  const version = (payload as { completed_version?: unknown }).completed_version;
  return (
    typeof version === "number" && version === ONBOARDING_COMPLETION_VERSION
  );
}

export interface OnboardingCompletionStore {
  get(): OnboardingCompleted;
  subscribe(listener: () => void): () => void;
  /** Read the saved answer once per launch. Later calls reuse the first. */
  load(): Promise<void>;
  /**
   * Finished the tour: true at once for this launch, and saved for the next.
   * Resolves once the save settles (success or failure) or after
   * `saveWaitMs`, whichever is first, and never rejects: the tour's ending
   * waits on it so a window closed right after Finish cannot abort the save,
   * and the bound keeps a hung request from stranding the finishing card.
   */
  markCompleted(): Promise<void>;
}

/** How long the tour's ending waits for the completion save, at most. */
export const COMPLETION_SAVE_WAIT_MS = 5000;

export function createOnboardingCompletionStore(
  io: {
    read: () => Promise<unknown>;
    write: (version: number) => Promise<void>;
  },
  saveWaitMs: number = COMPLETION_SAVE_WAIT_MS,
): OnboardingCompletionStore {
  let value: OnboardingCompleted = null;
  let loading: Promise<void> | null = null;
  const listeners = new Set<() => void>();
  const set = (next: boolean) => {
    if (value === next) return;
    value = next;
    for (const listener of [...listeners]) listener();
  };
  return {
    get: () => value,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    load() {
      if (!loading) {
        loading = io.read().then(
          (payload) => {
            // A tour finished while the read was in flight has already
            // answered; a read that began before it must not undo it.
            if (value === null) set(completedFromApi(payload));
          },
          () => {
            if (value === null) set(false);
          },
        );
      }
      return loading;
    },
    markCompleted() {
      set(true);
      const saved = io
        .write(ONBOARDING_COMPLETION_VERSION)
        .catch((error: unknown) => {
          // This launch already shows the tour as finished; the next one will
          // invite the user again. console.debug, not error: clientLog ships
          // errors to diagnostics as faults, and this is not one.
          console.debug("Finishing the tutorial could not be saved", error);
        });
      let timer: ReturnType<typeof setTimeout> | undefined;
      const bound = new Promise<void>((resolve) => {
        timer = setTimeout(resolve, saveWaitMs);
      });
      return Promise.race([saved, bound]).finally(() => clearTimeout(timer));
    },
  };
}

export const onboardingCompletion = createOnboardingCompletionStore({
  read: getOnboardingCompletion,
  write: saveOnboardingCompletion,
});

/**
 * The one place the tour marks itself finished (useOnboarding's ending),
 * which waits on the returned promise before it leaves the finishing state.
 */
export function markOnboardingCompleted(): Promise<void> {
  return onboardingCompletion.markCompleted();
}
