/**
 * User-facing definitions for the imported-DOCX preservation boundary.
 *
 * Help and onboarding intentionally share this copy: these concepts look
 * similar in the Export menu, but they make materially different promises.
 */
export type SourceOutputGuidanceId =
  | "exact-original"
  | "source-preserving"
  | "normalized"
  | "normalized-redline"
  | "pass-through-only";

export interface SourceOutputGuidance {
  id: SourceOutputGuidanceId;
  label: string;
  description: string;
}

export const SOURCE_OUTPUT_GUIDANCE: readonly SourceOutputGuidance[] = [
  {
    id: "exact-original",
    label: "Exact-original download",
    description:
      "The retained upload, byte for byte. It never includes Build-a-Spec edits and remains available whenever the source bytes are retained, including for pass-through-only documents.",
  },
  {
    id: "source-preserving",
    label: "Source-preserving patched export",
    description:
      "A clone of the uploaded DOCX with only the current version's server-approved body changes patched in place. If every required change cannot be proven safe, this choice is blocked rather than silently normalized.",
  },
  {
    id: "normalized",
    label: "Normalized export",
    description:
      "A newly generated DOCX built from Build-a-Spec's semantic content. It is an explicit alternative and does not promise to preserve the uploaded package's formatting, layout, headers, footers, or other OOXML details.",
  },
  {
    id: "normalized-redline",
    label: "Normalized redline",
    description:
      "Tracked changes between normalized extracted provisions or saved versions. It is not a redline of the uploaded Word package, and Reject All does not recreate the original upload.",
  },
  {
    id: "pass-through-only",
    label: "Pass-through-only document",
    description:
      "The exact original is retained and downloadable, but source-body mutations are disabled. Review status, provenance, and project metadata may remain editable; normalized export stays a separate, deliberate choice.",
  },
] as const;

export const SOURCE_CAPABILITY_GUIDANCE =
  "For imported body content, the server decides each element and operation separately. Controls follow the current server report, disabled controls show its exact reason, and a missing or ambiguous permission fails closed. Permissions are recomputed after edits, QC fixes, undo, and redo; status or metadata permission does not imply permission to rewrite Word body content.";
