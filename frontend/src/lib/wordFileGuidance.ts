/**
 * Working with Word files: the habits that keep an imported spec's tracked
 * changes and history straight (owner request, Abraham, 2026-10-08).
 *
 * Help's How to use tab renders this as one card (`HelpModal.WordFilesGuide`).
 * Every rule here follows from how the app behaves today, not from a wish:
 *
 * * the importer reads a file's pending tracked changes as accepted, and the
 *   primary export accepts them in its copy, so a reviewer's markup never
 *   survives as markup — clean the file in Word first;
 * * a project reopened from its `.baspec` keeps the original upload as the
 *   redline's starting point, while importing an exported file starts that
 *   history over — reopen, never re-import;
 * * a reviewer's tracked paragraph merge is read as two provisions, which the
 *   primary export then "re-splits" in the user's name — the troubleshooting
 *   row names the symptom;
 * * moving a provision that carries a Word comment refuses the tracked
 *   export (`moved_annotation`), while deleting and re-adding it does not.
 *
 * `frontend/tests/wordFileGuidance.test.ts` pins every UI label and server
 * message quoted here to the file that shows it, so renaming one fails the
 * suite instead of leaving this guide stale.
 */

/** One rule: a bold lead and an optional plain continuation. */
export interface WordFileRule {
  t: string;
  d?: string;
  /** Indented sub-points under the rule. */
  sub?: readonly string[];
}

export interface WordFileStage {
  id: string;
  title: string;
  /** `checklist` items are ticked off once; `steps` run in order. */
  kind: "checklist" | "steps" | "points";
  rules: readonly WordFileRule[];
  note?: { label: string; text: string; tone: "info" | "warn" };
}

export const WORD_FILES_SHORT_VERSION =
  "Clean every file in Word before its first import, import it once, " +
  "always reopen the .baspec, and treat exported Word files as outputs only.";

export const WORD_FILE_STAGES: readonly WordFileStage[] = [
  {
    id: "before-import",
    title: "Before the first import: clean the file in Word",
    kind: "checklist",
    rules: [
      {
        t: "Resolve every tracked change.",
        d: "Review → Accept → Accept All Changes, or go through them one by one with Accept and Reject. Build-a-Spec reads pending changes as accepted, so a change you meant to reject would be kept.",
      },
      {
        t: "Delete every comment.",
        d: "Review → Delete → Delete All Comments in Document. A comment on a provision you later move blocks the tracked export.",
      },
      {
        t: "Confirm it’s clean.",
        d: "Open Review → Reviewing Pane. It should show 0 revisions, including header and footer changes.",
      },
      {
        t: "Save it as a new copy,",
        d: "e.g. 21 13 13 - CLEAN.docx. Keep the marked-up file as your record of who asked for what.",
      },
    ],
    note: {
      label: "Note:",
      text: "Track Changes switched on with nothing pending is fine. Only pending changes and comments cause problems.",
      tone: "info",
    },
  },
  {
    id: "import-once",
    title: "Import once",
    kind: "checklist",
    rules: [
      {
        t: "New section in a project:",
        d: "Next section → Leave it unnamed → Import Spec with the clean copy.",
      },
      { t: "Save the .baspec right away." },
      {
        t: "Watch for either warning sign:",
        sub: [
          "the import notice “The master carries pending tracked changes…”",
          "Redline on your original greyed out in the Export menu",
        ],
      },
    ],
    note: {
      label: "If you see either one:",
      text: "start that section over from a cleaned copy, before you edit anything.",
      tone: "warn",
    },
  },
  {
    id: "every-session",
    title: "Every session after that",
    kind: "checklist",
    rules: [
      {
        t: "Open the .baspec.",
        d: "Never import an exported Word file back in: that starts the section’s history over, and every tracked change in the file is read as accepted.",
      },
      {
        t: "Save the .baspec at the end of every session, and back it up.",
        d: "It holds your original upload and your full history, so every export shows every change since the first import.",
      },
    ],
    note: {
      label: "Note:",
      text: "A section with content can’t take a second import. Importing a file means a new section, with its history starting over.",
      tone: "info",
    },
  },
  {
    id: "editing",
    title: "Editing",
    kind: "points",
    rules: [
      { t: "Make all edits in Build-a-Spec,", d: "in the chat or the panel." },
      {
        t: "To reorder a provision that has a Word comment, delete it and add it back",
        d: "where you want it. A move can block the export. This only applies if you kept comments.",
      },
    ],
  },
];

/** Which export answers which need. */
export const WORD_FILE_EXPORTS: readonly { need: string; use: string }[] = [
  {
    need: "Your formatting, every change since the original import tracked, Track Changes on for Word edits",
    use: "Export Word - Tracked Changes ON",
  },
  {
    need: "The same, but keeping your file’s original Track Changes setting",
    use: "Redline on your original",
  },
  {
    need: "Changes since an earlier version, in Build-a-Spec’s styles",
    use: "Redline vs version… (pick the version in Compare mode)",
  },
  {
    need: "Changes since the last issue, in your own formatting",
    use: "Word: Review → Compare → Compare…, with the previous issued export as the original and the new export as the revised",
  },
  { need: "Your upload back, unchanged", use: "Download exact original DOCX" },
];

export const WORD_FILE_EXPORT_RULES: readonly WordFileRule[] = [
  { t: "Exported files are outputs.", d: "Don’t import them back." },
  {
    t: "Every tracked change appears under your export author name,",
    d: "dated at export and measured against the original master.",
  },
];

/** When markup comes back on an exported file — yours in Word, or a reviewer’s. */
export const WORD_FILE_MARKUP_STEPS: readonly WordFileRule[] = [
  { t: "Don’t import the file." },
  {
    t: "Resolve the markup in Word",
    d: "(accept or reject), and keep the marked-up file as your record.",
  },
  {
    t: "Enter the decisions in Build-a-Spec,",
    d: "either:",
    sub: [
      "by hand, or",
      "Attach Document with the resolved file and ask the chat to bring the spec in line with it.",
    ],
  },
  { t: "Check the next Tracked Changes ON export", d: "against the markup." },
  { t: "Save the .baspec." },
];

export const WORD_FILE_DO: readonly string[] = [
  "Clean files in Word before the first import",
  "Reopen through the .baspec",
  "Save and back up the .baspec every session",
  "Delete and re-add a commented provision to reorder it",
];

export const WORD_FILE_DONT: readonly string[] = [
  "Import a file with pending tracked changes",
  "Import an exported file back in",
  "Expect edits made in Word to flow back on their own",
  "Ignore the pending-tracked-changes import notice",
];

export const WORD_FILE_TROUBLESHOOTING: readonly { see: string; fix: string }[] = [
  {
    see: "The import notice about pending tracked changes, or Redline on your original greyed out",
    fix: "Start the section over from a cleaned copy, before editing.",
  },
  {
    see: "A provision starting mid-sentence (a comma or lowercase word) after one missing its ending",
    fix: "A reviewer’s tracked paragraph merge, read as two provisions. Start over from a cleaned copy.",
  },
  {
    see: "Tracked changes in your export that you didn’t make",
    fix: "Same cause, same fix.",
  },
  {
    see: "Export refused: “A provision you moved carries a comment…”",
    fix: "Undo the move, then delete the provision and add it back where you want it.",
  },
];
