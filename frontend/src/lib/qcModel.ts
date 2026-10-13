/**
 * Display name for the model Final QC runs on.
 *
 * `BUILD_A_SPEC_QC_MODEL` can override the default, and the drawer's cost
 * line and the pre-flight confirmation are paid-run consent copy: they must
 * name the model the backend will actually call, never a hardcoded default.
 * `strongerThanDrafter` gates the "stronger reviewer than the drafter"
 * claim. It compares the lens model with the model that drafts (the chat's,
 * `health.model`), tier against tier, so it is true only where the review
 * really is a stronger model: Opus 5.5 reviewing a Sonnet 5.5 draft claims
 * it, Opus 5.5 reviewing its own draft (the chat's default since
 * 2026-10-13) does not, and an unrecognized model on either side never does.
 */
export const DEFAULT_QC_MODEL = "claude-opus-5-5";

/** The chat's model when health has not said (`INTERVIEW_MODEL_DEFAULT`,
 *  Opus 5.5 since 2026-10-13; `BUILD_A_SPEC_INTERVIEW_MODEL`). */
export const DEFAULT_DRAFTER_MODEL = "claude-opus-5-5";

// tier: Haiku 1 < Sonnet 2 < Opus 3 < Fable 4. Only a strictly higher tier
// is "stronger"; a newer model within a tier makes no claim.
const KNOWN: Record<string, { name: string; tier: number }> = {
  "claude-fable-5": { name: "Claude Fable 5", tier: 4 },
  "claude-opus-5-5": { name: "Claude Opus 5.5", tier: 3 },
  "claude-opus-5": { name: "Claude Opus 5", tier: 3 },
  "claude-opus-4-8": { name: "Claude Opus 4.8", tier: 3 },
  "claude-sonnet-5-5": { name: "Claude Sonnet 5.5", tier: 2 },
  "claude-sonnet-5": { name: "Claude Sonnet 5", tier: 2 },
  "claude-haiku-5-5": { name: "Claude Haiku 5.5", tier: 1 },
};

/** The model Final QC's verifier seats run on by default (since
 *  2026-10-08; `BUILD_A_SPEC_QC_VERIFIER_MODEL`). The lenses keep
 *  `DEFAULT_QC_MODEL`. */
export const DEFAULT_QC_VERIFIER_MODEL = "claude-sonnet-5-5";

export interface QcModelLabel {
  id: string;
  name: string;
  strongerThanDrafter: boolean;
}

/** `id` absent (health not loaded yet, or an older backend) reads as the
 *  shipped default; an unrecognized id is shown verbatim and makes no
 *  strength claim. `drafterId` is the chat's model, read the same way. */
export function qcModelLabel(
  id?: string | null,
  drafterId?: string | null,
): QcModelLabel {
  const resolved = resolve(id, DEFAULT_QC_MODEL);
  const lenses = KNOWN[resolved];
  const drafter = KNOWN[resolve(drafterId, DEFAULT_DRAFTER_MODEL)];
  return {
    ...labelFor(resolved),
    strongerThanDrafter:
      lenses !== undefined && drafter !== undefined && lenses.tier > drafter.tier,
  };
}

/** The verifier seats' model, named the same way: `id` absent reads as the
 *  shipped seat default. Seats make no claim about the drafter. */
export function qcVerifierModelLabel(id?: string | null): QcModelLabel {
  return labelFor(resolve(id, DEFAULT_QC_VERIFIER_MODEL));
}

/** The chat's model, named the same way: `id` absent reads as the shipped
 *  chat default. */
export function drafterModelLabel(id?: string | null): QcModelLabel {
  return labelFor(resolve(id, DEFAULT_DRAFTER_MODEL));
}

/** The drawer's paid-run consent line: the lenses' model, and the seats'
 *  model when it differs, both as configured. */
export function qcRunsOnCopy(
  qcModel?: string | null,
  qcVerifierModel?: string | null,
  drafterModel?: string | null,
): string {
  const lenses = qcModelLabel(qcModel, drafterModel);
  const seats = qcVerifierModelLabel(qcVerifierModel);
  const lead = lenses.strongerThanDrafter
    ? `Runs on ${lenses.name} — a stronger reviewer than the drafter`
    : `Runs on ${lenses.name}`;
  return seats.id === lenses.id
    ? `${lead}.`
    : `${lead}; ${seats.name} reviewers then check each finding.`;
}

function resolve(id: string | null | undefined, fallback: string): string {
  return (id ?? "").trim() || fallback;
}

function labelFor(resolved: string): QcModelLabel {
  const known = KNOWN[resolved];
  return {
    id: resolved,
    name: known ? known.name : resolved,
    strongerThanDrafter: false,
  };
}
