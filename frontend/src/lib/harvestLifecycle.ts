/**
 * Session-local harvest lifecycle. Visibility belongs to ArtifactPanel;
 * closing a modal is not a decision to discard its paid work. The dialog
 * keeps this phase (and its edited rows) until App replaces the session.
 */
import type { HarvestCommitResult, HarvestPreview, HarvestStatus } from "../types";
import { canHarvest, harvestHint } from "./harvest.ts";

export type HarvestPhase =
  | { kind: "intro" }
  | { kind: "running" }
  | { kind: "review"; preview: HarvestPreview }
  | { kind: "failed"; message: string; code: string }
  | { kind: "done"; result: HarvestCommitResult; accepted: number };

export type HarvestActivity = "idle" | "running" | "ready";

/** The caller stores this phase synchronously, before invoking the paid
 * handler: a second click before React renders must see "running" too. */
export function beginHarvestRun(phase: HarvestPhase): HarvestPhase | null {
  if (phase.kind === "intro") return { kind: "running" };
  if (
    phase.kind === "failed" &&
    phase.code !== "tutorial_active" &&
    phase.code !== "nothing_to_harvest"
  ) return { kind: "running" };
  return null;
}

/** Closing preserves a running call, a sheet, and a failure. A completed
 * commit consumed the token, so the next visit may offer a new harvest. */
export function closeHarvestPhase(phase: HarvestPhase): HarvestPhase {
  return phase.kind === "done" ? { kind: "intro" } : phase;
}

export function harvestActivity(phase: HarvestPhase): HarvestActivity {
  if (phase.kind === "running") return "running";
  if (phase.kind === "review") return "ready";
  return "idle";
}

/** All three doors show retained work before the unread-reply hint. Even a
 * zero-proposal sheet is ready: choosing to record none marks replies read. */
export function harvestDoorHint(
  status: HarvestStatus | null,
  activity: HarvestActivity,
): string {
  if (activity === "running") return "Fact harvest is still running";
  if (activity === "ready") return "Fact harvest is ready to review";
  return harvestHint(status);
}

/** Reopening existing work doesn't spend, and must remain possible even
 * when the current draft/reply payload no longer offers a fresh harvest. */
export function canOpenHarvest(
  status: HarvestStatus | null,
  activity: HarvestActivity,
): boolean {
  return activity !== "idle" || canHarvest(status);
}
