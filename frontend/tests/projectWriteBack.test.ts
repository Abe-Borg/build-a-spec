/**
 * The brief is a living file (Project workspace Phase 3) — the frontend half.
 *
 * Pinned at the source level where there is no DOM harness (the
 * projectPanel.test.ts idiom), and as plain units for the pure helpers:
 *
 * 1. Update project brief and Pull project changes each declare their
 *    capability, are hidden in a tour and locked while anything runs.
 * 2. Pull re-reads research, Final QC and readiness after it installs —
 *    new facts and documents change what a retained review read, and the
 *    drawers must not keep claiming otherwise.
 * 3. The client never words a difference: the notice shows the server's
 *    lines verbatim (projectMerge.ts only counts).
 * 4. A save that could not refresh the brief still reads as a save, and
 *    says why the brief was not updated.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  describeBriefRefresh,
  describePull,
  describePullOffer,
  pullNoticeLines,
} from "../src/lib/projectMerge.ts";
import type { MergeReport } from "../src/types";

const here = dirname(fileURLToPath(import.meta.url));
const read = (...parts: string[]) =>
  readFileSync(join(here, "..", "src", ...parts), "utf8");

const app = read("App.tsx");
const panel = read("components", "ProjectPanel.tsx");
const artifact = read("components", "ArtifactPanel.tsx");
const api = read("lib", "api.ts");
const capabilities = read("lib", "capabilities.ts");

function report(overrides: Partial<MergeReport> = {}): MergeReport {
  return {
    research: {
      rounds_added: 0,
      items_added: 0,
      items_confirmed: 0,
      legacy_rounds: 0,
      first_found_only: 0,
      carried_without_round: 0,
    },
    references: { added: 0, already_present: 0, re_minted: 0, dropped: [] },
    facts: {
      added: 0,
      confirmed: 0,
      updated: 0,
      retired: 0,
      folded: 0,
      history_added: 0,
      re_minted: 0,
      refs_rewritten: 0,
      refs_unresolved: 0,
      conflicts: [],
    },
    sections: { added: 0, updated: 0 },
    setup: [],
    conflicts: [],
    warnings: [],
    changed: false,
    assets_changed: false,
    ...overrides,
  };
}

test("both write-back controls declare their capability, hide in a tour, and lock while busy", () => {
  assert.match(capabilities, /"project\.brief-refresh"/);
  assert.match(capabilities, /"project\.pull"/);
  // Both need the folder and never appear on a tour's practice copy.
  assert.match(panel, /const writeBack = !!where && !tutorialActive;/);
  assert.match(panel, /const pullOffered = writeBack && !!listing\?\.pull_available;/);

  const refresh = /\{writeBack && \(\s*<button[\s\S]*?<\/button>\s*\)\}/.exec(panel)?.[0];
  assert.ok(refresh, "Update project brief must be gated on writeBack");
  assert.match(refresh, /data-capability="project\.brief-refresh"/);
  assert.match(refresh, /disabled=\{busy \|\| acting\}/);
  assert.match(refresh, /run\("refresh"\)/);

  const pull = /\{pullOffered && \(\s*<div[\s\S]*?<\/div>\s*\)\}/.exec(panel)?.[0];
  assert.ok(pull, "Pull project changes must be gated on pullOffered");
  assert.match(pull, /data-capability="project\.pull"/);
  assert.match(pull, /disabled=\{busy \|\| acting\}/);
  assert.match(pull, /run\("pull"\)/);

  // App owns the calls; the panel only reports what they resolved.
  assert.match(artifact, /onRefreshBrief=\{onRefreshProjectBrief\}/);
  assert.match(artifact, /onPull=\{onPullProject\}/);
  assert.match(app, /onRefreshProjectBrief=\{onRefreshProjectBrief\}/);
  assert.match(app, /onPullProject=\{onPullProject\}/);
});

test("the client posts to the two routes and throws the server's own refusal", () => {
  const refresh = /export async function refreshProjectBrief[\s\S]*?\n\}/.exec(api)?.[0];
  assert.ok(refresh, "refreshProjectBrief must exist");
  assert.match(refresh, /fetch\("\/api\/project\/brief\/refresh", \{ method: "POST" \}\)/);
  assert.match(refresh, /throw new Error\(data\.error \?\?/);
  const pull = /export async function pullProject[\s\S]*?\n\}/.exec(api)?.[0];
  assert.ok(pull, "pullProject must exist");
  assert.match(pull, /fetch\("\/api\/project\/pull", \{ method: "POST" \}\)/);
  assert.match(pull, /throw new Error\(data\.error \?\?/);
});

test("a pull applies the payload, then re-reads research, Final QC and readiness", () => {
  const pull = /const onPullProject = async[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(pull, "onPullProject must exist");
  assert.match(pull, /if \(!applyDocPayload\(result\)\) return "";/);
  const applied = pull.indexOf("applyDocPayload(result)");
  for (const call of ["refreshResearch()", "refreshQc()", "refreshReadiness()"]) {
    const at = pull.indexOf(call);
    assert.ok(at > applied, `${call} must run after the payload is applied`);
  }
  assert.match(pull, /setProjectSectionsNonce\(\(n\) => n \+ 1\)/);
  // Differences are SHOWN in the server's words, never applied.
  assert.match(pull, /const lines = pullNoticeLines\(result\.report\);/);
  // An update re-reads the link it moved.
  const refresh = /const onRefreshProjectBrief = async[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(refresh, "onRefreshProjectBrief must exist");
  assert.match(refresh, /refreshDoc\(\);/);
});

test("a save whose brief could not be refreshed still reads as a save, and says why", () => {
  const save = /const saveProjectFile = async[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(save, "saveProjectFile must exist");
  assert.match(save, /if \(result\.brief_error\) \{/);
  assert.match(save, /title: "Saved — but the project brief was not updated"/);
  assert.match(save, /lines: \[result\.brief_error\]/);
  // ok is the save's own answer, never the brief's.
  assert.match(save, /ok: !!result\?\.ok,/);
});

test("the refresh line counts what was added and names edition disagreements", () => {
  assert.equal(
    describeBriefRefresh(report(), false),
    "The project brief already had everything this section holds.",
  );
  assert.equal(
    describeBriefRefresh(
      report({
        facts: { ...report().facts, added: 2 },
        research: { ...report().research, rounds_added: 1 },
      }),
      true,
    ),
    "Project brief updated: added 2 facts and 1 research round.",
  );
  assert.equal(
    describeBriefRefresh(
      report({
        references: { added: 1, already_present: 0, re_minted: 0, dropped: [] },
        setup: [
          {
            kind: "edition",
            field: "NFPA 13",
            label: "NFPA 13 edition",
            brief: "2022",
            section: "2025",
            kept: "the section",
          },
        ],
      }),
      true,
    ),
    "Project brief updated: added 1 document. 1 edition disagreement recorded as project facts to resolve.",
  );
  assert.equal(describeBriefRefresh(report(), true), "Project brief updated.");
});

test("the pull lines count, and the notice lines are the server's, each once", () => {
  assert.equal(
    describePull({ rounds: 1, references: 2, facts: 3 }),
    "Pulled project changes: 3 fact changes, 1 research round and 2 documents.",
  );
  // A pull that only edited or retired facts is not "nothing new" (Codex,
  // PR #179): the count includes in-place changes, and says so.
  assert.equal(
    describePull({ rounds: 0, references: 0, facts: 1 }),
    "Pulled project changes: 1 fact change.",
  );
  assert.equal(describePull({ rounds: 0, references: 0, facts: 0 }), "Pulled project changes: nothing new to bring in.");
  assert.equal(
    describePullOffer({ available: true, rounds: 0, references: 1, facts: 2 }),
    "Other sections added 2 fact changes and 1 document since this section last synced.",
  );
  assert.equal(
    describePullOffer(null),
    "The project brief holds work this section has not pulled yet.",
  );
  const lines = pullNoticeLines(
    report({
      conflicts: ["The project brief records NFPA 13 2025; section 21 13 13 records 2022."],
      warnings: [
        "The city differs: the project brief records 'Leesburg'; section 21 13 13 records 'Ashburn'.",
        "The project brief records NFPA 13 2025; section 21 13 13 records 2022.",
      ],
    }),
  );
  assert.deepEqual(lines, [
    "The project brief records NFPA 13 2025; section 21 13 13 records 2022.",
    "The city differs: the project brief records 'Leesburg'; section 21 13 13 records 'Ashburn'.",
  ]);
  assert.deepEqual(pullNoticeLines(null), []);
});
