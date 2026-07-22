/**
 * "Has this user finished the guided tour?" — the codebase's first (and
 * only) localStorage use. Purely cosmetic: it drives the starter chip's
 * sub-line and pulse, never gates re-entry (the Header's Tour button and
 * the chip both restart the tour regardless). Wrapped in try/catch because
 * the pywebview WebView2 origin may restrict storage — losing it costs
 * nothing but a pulse.
 */
const KEY = "build-a-spec:onboarding-completed";

export function hasCompletedOnboarding(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function markOnboardingCompleted(): void {
  try {
    localStorage.setItem(KEY, "1");
  } catch {
    // Storage unavailable — cosmetic only, nothing to recover.
  }
}
