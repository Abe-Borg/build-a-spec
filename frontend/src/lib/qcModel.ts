/**
 * Display name for the model Final QC runs on.
 *
 * `BUILD_A_SPEC_QC_MODEL` can override the default, and the drawer's cost
 * line and the pre-flight confirmation are paid-run consent copy: they must
 * name the model the backend will actually call, never a hardcoded default.
 * `strongerThanDrafter` gates the "stronger reviewer than the drafter"
 * claim, which is true of the Opus/Fable models and false of anything else
 * an operator might configure.
 */
export const DEFAULT_QC_MODEL = "claude-opus-5-5";

const KNOWN: Record<string, { name: string; strongerThanDrafter: boolean }> = {
  "claude-opus-5-5": { name: "Claude Opus 5.5", strongerThanDrafter: true },
  "claude-opus-5": { name: "Claude Opus 5", strongerThanDrafter: true },
  "claude-opus-4-8": { name: "Claude Opus 4.8", strongerThanDrafter: true },
  "claude-fable-5": { name: "Claude Fable 5", strongerThanDrafter: true },
  "claude-sonnet-5-5": { name: "Claude Sonnet 5.5", strongerThanDrafter: false },
  "claude-sonnet-5": { name: "Claude Sonnet 5", strongerThanDrafter: false },
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
 *  strength claim. */
export function qcModelLabel(id?: string | null): QcModelLabel {
  return labelFor((id ?? "").trim() || DEFAULT_QC_MODEL);
}

/** The verifier seats' model, named the same way: `id` absent reads as the
 *  shipped seat default. */
export function qcVerifierModelLabel(id?: string | null): QcModelLabel {
  return labelFor((id ?? "").trim() || DEFAULT_QC_VERIFIER_MODEL);
}

/** The drawer's paid-run consent line: the lenses' model, and the seats'
 *  model when it differs, both as configured. */
export function qcRunsOnCopy(
  qcModel?: string | null,
  qcVerifierModel?: string | null,
): string {
  const lenses = qcModelLabel(qcModel);
  const seats = qcVerifierModelLabel(qcVerifierModel);
  const lead = lenses.strongerThanDrafter
    ? `Runs on ${lenses.name} — a stronger reviewer than the drafter`
    : `Runs on ${lenses.name}`;
  return seats.id === lenses.id
    ? `${lead}.`
    : `${lead}; ${seats.name} reviewers then check each finding.`;
}

function labelFor(resolved: string): QcModelLabel {
  const known = KNOWN[resolved];
  if (known) return { id: resolved, ...known };
  return { id: resolved, name: resolved, strongerThanDrafter: false };
}
