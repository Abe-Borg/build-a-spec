/**
 * Completion and resumable tutorial progress. Completion is cosmetic; the
 * versioned progress record only reconnects to a server-confirmed protected
 * workspace. Both are wrapped because a WebView origin may restrict storage.
 */
const COMPLETED_KEY = "build-a-spec:onboarding-completed";
const PROGRESS_KEY = "build-a-spec:onboarding-progress";
export const ONBOARDING_COMPLETION_VERSION = 2;

export interface StoredOnboardingProgress {
  version: number;
  tutorialId: string;
  workspaceId: number;
  generation: number;
  chunk: number;
  step: number;
}

export function hasCompletedOnboarding(): boolean {
  try {
    return (
      localStorage.getItem(COMPLETED_KEY) ===
      String(ONBOARDING_COMPLETION_VERSION)
    );
  } catch {
    return false;
  }
}

export function markOnboardingCompleted(): void {
  try {
    localStorage.setItem(COMPLETED_KEY, String(ONBOARDING_COMPLETION_VERSION));
  } catch {
    // Storage unavailable — cosmetic only, nothing to recover.
  }
}

/**
 * Completion version 1 represented the former shortened tour. Surface one
 * invitation to the restored full tutorial, then remember that it was shown
 * without falsely marking the new tutorial complete.
 */
export function consumeTutorialUpdateInvitation(): boolean {
  try {
    if (localStorage.getItem(COMPLETED_KEY) !== "1") return false;
    localStorage.setItem(COMPLETED_KEY, "legacy-invitation-shown");
    return true;
  } catch {
    return false;
  }
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
