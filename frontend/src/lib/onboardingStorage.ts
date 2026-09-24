/**
 * The guided tour's resumable progress record. It only has to survive a
 * reload within one launch — the tutorial workspace it reconnects to lives in
 * server memory, so it cannot outlive the launch anyway — which is exactly
 * what browser storage can do in the packaged app (pywebview's private mode,
 * a fresh port per launch). Wrapped because a WebView origin may restrict
 * storage.
 *
 * Completion is NOT here: it has to outlive the launch, so the server keeps
 * it (lib/onboardingCompletion.ts, backend/onboarding_state.py).
 */
const PROGRESS_KEY = "build-a-spec:onboarding-progress";

export interface StoredOnboardingProgress {
  version: number;
  tutorialId: string;
  workspaceId: number;
  generation: number;
  chunk: number;
  step: number;
}

export function loadOnboardingProgress(
  expectedVersion: number,
): StoredOnboardingProgress | null {
  try {
    const raw = localStorage.getItem(PROGRESS_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as StoredOnboardingProgress;
    if (
      value.version !== expectedVersion ||
      !value.tutorialId ||
      !Number.isInteger(value.workspaceId) ||
      !Number.isInteger(value.generation) ||
      !Number.isInteger(value.chunk) ||
      !Number.isInteger(value.step)
    ) {
      localStorage.removeItem(PROGRESS_KEY);
      return null;
    }
    return value;
  } catch {
    return null;
  }
}

export function saveOnboardingProgress(
  progress: StoredOnboardingProgress,
): void {
  try {
    localStorage.setItem(PROGRESS_KEY, JSON.stringify(progress));
  } catch {
    // A blocked WebView origin only loses cross-launch resume state.
  }
}

export function clearOnboardingProgress(): void {
  try {
    localStorage.removeItem(PROGRESS_KEY);
  } catch {
    // Cosmetic state only.
  }
}
