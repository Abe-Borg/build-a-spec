/**
 * Pure helpers for the fact harvest (Project workspace Phase 4).
 *
 * The dialog renders; these decide. The rule that matters most — a proposal
 * the user did not tick is never sent, not even its edits — is a function a
 * test can call rather than a click handler it can only read. The server is
 * still the judge of every proposal at commit (the store's field rules and
 * the source resolver); nothing here second-guesses it.
 */
import type {
  HarvestPreview,
  HarvestProposal,
  HarvestStatus,
} from "../types";
import type { HarvestCommitInput, HarvestEditableField } from "./api";

/** The fields a user may change before accepting. Never the evidence: the
 *  quoted line is the model's claim about the session, and editing it would
 *  turn a check a human can make into one nobody can. */
export const HARVEST_EDITABLE_FIELDS: readonly HarvestEditableField[] = [
  "statement",
  "detail",
  "scope",
  "section",
  "status",
  "source_kind",
  "source_ref",
];

export type HarvestDraft = Record<HarvestEditableField, string>;

export function draftOfProposal(proposal: HarvestProposal): HarvestDraft {
  return {
    statement: proposal.statement,
    detail: proposal.detail,
    scope: proposal.scope,
    section: proposal.section,
    status: proposal.status,
    source_kind: proposal.source_kind,
    source_ref: proposal.source_ref,
  };
}

/** The rows ticked when the sheet opens: every proposal the server found
 *  nothing wrong with. A proposal with a problem starts unchecked, with the
 *  reason beside it — the user may tick it after fixing it, and the commit
 *  re-checks it either way. */
export function initialAccepted(proposals: readonly HarvestProposal[]): Set<number> {
  return new Set(
    proposals.filter((proposal) => !proposal.problem).map((proposal) => proposal.index),
  );
}

/**
 * The commit payload: only ticked rows, each once, in the order the sheet
 * showed them, and for each only the fields that differ from what the model
 * proposed. An unticked row never appears — not its index, not its edits —
 * so a half-edited proposal the user then set aside cannot ride along.
 */
export function buildHarvestCommit(
  token: string,
  proposals: readonly HarvestProposal[],
  accepted: ReadonlySet<number>,
  drafts: ReadonlyMap<number, HarvestDraft>,
): HarvestCommitInput {
  const picked = proposals.filter((proposal) => accepted.has(proposal.index));
  const edits: HarvestCommitInput["edits"] = {};
  for (const proposal of picked) {
    const draft = drafts.get(proposal.index);
    if (!draft) continue;
    const original = draftOfProposal(proposal);
    const changed: Partial<Record<HarvestEditableField, string>> = {};
    for (const field of HARVEST_EDITABLE_FIELDS) {
      if (draft[field] !== original[field]) changed[field] = draft[field];
    }
    if (Object.keys(changed).length > 0) edits[String(proposal.index)] = changed;
  }
  return {
    token,
    accepted: picked.map((proposal) => proposal.index),
    edits,
  };
}

const plural = (count: number, one: string, many = `${one}s`) =>
  `${count} ${count === 1 ? one : many}`;

/** "3 replies since facts were last harvested" — or "" when nothing is
 *  waiting. The ONE wording the three hint sites share (the Project facts
 *  panel, Next section, and the brief export), so they cannot drift. A
 *  conversation never harvested says so instead of "since … last". */
export function harvestHint(status: HarvestStatus | null | undefined): string {
  if (!status || status.replies_since <= 0) return "";
  const replies = plural(status.replies_since, "reply", "replies");
  return status.last_bubble > 0
    ? `${replies} since facts were last harvested`
    : `${replies} not yet harvested for facts`;
}

/** Whether the harvest has anything to read — the server's `harvestable`: a
 *  reply since the last harvest, a provision, or a Final QC dismissal
 *  reason, the route's own check. The panel's door follows this rather than
 *  the reply hint, so a section with a draft but no reply to read (an
 *  imported master edited by hand) can still be harvested. Unknown (no
 *  payload yet) reads as closed. */
export function canHarvest(status: HarvestStatus | null | undefined): boolean {
  return status?.harvestable === true;
}

/** What the call read, one line for the sheet's header. */
export function describeHarvestRead(preview: HarvestPreview): string {
  const parts: string[] = [];
  if (preview.turns_read > 0) {
    parts.push(
      preview.first_turn === preview.last_turn
        ? `reply ${preview.last_turn} of ${preview.replies_total}`
        : `replies ${preview.first_turn}–${preview.last_turn} of ${preview.replies_total}`,
    );
  } else if (preview.replies_total > 0) {
    parts.push(`no new replies (all ${preview.replies_total} were read before)`);
  }
  if (preview.provisions > 0) parts.push(plural(preview.provisions, "provision"));
  if (preview.dismissals > 0) {
    parts.push(plural(preview.dismissals, "Final QC dismissal reason"));
  }
  const read =
    parts.length === 0
      ? "Read nothing new"
      : parts.length === 1
        ? `Read ${parts[0]}`
        : `Read ${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  if (preview.turns_dropped > 0) {
    return `${read} — the ${plural(preview.turns_dropped, "oldest unread reply", "oldest unread replies")} ${
      preview.turns_dropped === 1 ? "was" : "were"
    } left out for length.`;
  }
  if (preview.transcript_truncated) {
    return `${read} — the start of one long reply was left out for length.`;
  }
  return `${read}.`;
}

/** The preview call's cost, as the meter estimates it. */
export function formatHarvestCost(usd: number | null | undefined): string {
  if (typeof usd !== "number" || !Number.isFinite(usd)) return "";
  return usd < 0.01 ? "under $0.01" : `≈ $${usd.toFixed(2)}`;
}

/** The commit button: recording nothing is an answer too — it marks the
 *  replies read, so the next harvest starts after them. */
export function commitLabel(count: number): string {
  return count === 0
    ? "Record none — mark these replies read"
    : `Record ${plural(count, "fact")}`;
}
