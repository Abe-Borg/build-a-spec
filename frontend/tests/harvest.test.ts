/**
 * The fact harvest's frontend (Project workspace Phase 4).
 *
 * Pure helpers are called directly: the rule that matters most — a proposal
 * the user did not tick is never sent, not even its edits — lives in
 * `buildHarvestCommit`, so it is asserted as behaviour rather than read off a
 * click handler. The API client is exercised against a stubbed fetch. The
 * wiring (App refreshes readiness AND Final QC after a commit; every door
 * only OPENS the dialog; the dialog never runs on mount) is pinned at the
 * source level — the `tour.test.ts` idiom, since these components have no
 * DOM harness.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  HarvestRequestError,
  commitFactHarvest,
  runFactHarvest,
} from "../src/lib/api.ts";
import {
  buildHarvestCommit,
  canHarvest,
  commitLabel,
  describeHarvestRead,
  draftOfProposal,
  formatHarvestCost,
  harvestHint,
  initialAccepted,
} from "../src/lib/harvest.ts";
import type { HarvestPreview, HarvestProposal } from "../src/types.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

function proposal(index: number, overrides: Partial<HarvestProposal> = {}): HarvestProposal {
  return {
    index,
    statement: `Fact ${index}.`,
    detail: "",
    scope: "project",
    section: "",
    status: "confirmed",
    source_kind: "user",
    source_ref: `turn:${index + 1}`,
    evidence: `Line ${index}.`,
    problem: "",
    evidence_found: true,
    ...overrides,
  };
}

function preview(overrides: Partial<HarvestPreview> = {}): HarvestPreview {
  return {
    ok: true,
    token: "tok",
    proposals: [],
    dropped_duplicates: 0,
    transcript_truncated: false,
    turns_read: 0,
    turns_dropped: 0,
    first_turn: 0,
    last_turn: 0,
    replies_total: 0,
    since_bubble: 0,
    provisions: 0,
    dismissals: 0,
    usage: {},
    estimated_cost_usd: 0,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// The sheet's rules
// ---------------------------------------------------------------------------

test("an unticked proposal is never sent — not its index, not its edits", () => {
  const proposals = [proposal(0), proposal(1), proposal(2)];
  const drafts = new Map(proposals.map((p) => [p.index, draftOfProposal(p)]));
  // The user edits proposal 1, then sets it aside; edits proposal 2 and keeps it.
  drafts.set(1, { ...draftOfProposal(proposals[1]), statement: "Edited then unticked." });
  drafts.set(2, { ...draftOfProposal(proposals[2]), source_ref: "", detail: "Said on the call." });

  const commit = buildHarvestCommit("tok", proposals, new Set([2, 0]), drafts);

  assert.equal(commit.token, "tok");
  assert.deepEqual(commit.accepted, [0, 2], "sheet order, each once, unticked absent");
  assert.deepEqual(commit.edits, {
    "2": { detail: "Said on the call.", source_ref: "" },
  });
  assert.ok(!("1" in commit.edits), "an unticked row's edits never ride along");
});

test("an untouched ticked row sends no edit, and ticking nothing is a valid answer", () => {
  const proposals = [proposal(0), proposal(1)];
  const drafts = new Map(proposals.map((p) => [p.index, draftOfProposal(p)]));
  const some = buildHarvestCommit("tok", proposals, new Set([1]), drafts);
  assert.deepEqual(some, { token: "tok", accepted: [1], edits: {} });

  const none = buildHarvestCommit("tok", proposals, new Set(), drafts);
  assert.deepEqual(none, { token: "tok", accepted: [], edits: {} });
  assert.equal(commitLabel(0), "Record none — mark these replies read");
  assert.equal(commitLabel(1), "Record 1 fact");
  assert.equal(commitLabel(3), "Record 3 facts");
});

test("a proposal the server found a problem with starts unchecked", () => {
  const proposals = [
    proposal(0),
    proposal(1, { problem: "source_ref 'r-9' names no finding in this section's research profile." }),
    proposal(2),
  ];
  assert.deepEqual([...initialAccepted(proposals)].sort(), [0, 2]);
});

test("the hint is one wording, and says nothing when nothing is waiting", () => {
  assert.equal(harvestHint(null), "");
  assert.equal(harvestHint({ replies_since: 0, last_bubble: 4, replies_total: 4 }), "");
  assert.equal(
    harvestHint({ replies_since: 1, last_bubble: 3, replies_total: 4 }),
    "1 reply since facts were last harvested",
  );
  assert.equal(
    harvestHint({ replies_since: 5, last_bubble: 0, replies_total: 5 }),
    "5 replies not yet harvested for facts",
  );
});

test("the door follows what the harvest can read, not the reply hint", () => {
  // Unknown (no payload yet) and nothing-to-read keep the door closed.
  assert.equal(canHarvest(null), false);
  assert.equal(
    canHarvest({ replies_since: 0, last_bubble: 0, replies_total: 0, harvestable: false }),
    false,
  );
  // A draft with no reply to read (an imported master edited by hand) is
  // still worth harvesting — the server says so, and there is no hint.
  const draftOnly = { replies_since: 0, last_bubble: 0, replies_total: 0, harvestable: true };
  assert.equal(canHarvest(draftOnly), true);
  assert.equal(harvestHint(draftOnly), "");

  // The panel renders, and its button is enabled, on that flag — never on
  // the hint alone, which would hide the only unconditional door.
  const panel = read("../src/components/ProjectFactsPanel.tsx");
  assert.match(panel, /const harvestable = harvestAvailable && canHarvest\(harvest\);/);
  assert.match(panel, /if \(items\.length === 0 && link === null && !harvestable\) return null;/);
  assert.doesNotMatch(panel, /&& !hint\) return null/);
  assert.match(panel, /disabled=\{busy \|\| !harvestable\}\s*onClick=\{onHarvest\}/);
});

test("the sheet's header says what was read, and what was left out", () => {
  assert.equal(
    describeHarvestRead(
      preview({ turns_read: 5, first_turn: 3, last_turn: 7, replies_total: 7, provisions: 42, dismissals: 1 }),
    ),
    "Read replies 3–7 of 7, 42 provisions and 1 Final QC dismissal reason.",
  );
  assert.equal(
    describeHarvestRead(preview({ turns_read: 1, first_turn: 2, last_turn: 2, replies_total: 2 })),
    "Read reply 2 of 2.",
  );
  assert.equal(
    describeHarvestRead(preview({ replies_total: 4, provisions: 1 })),
    "Read no new replies (all 4 were read before) and 1 provision.",
  );
  assert.equal(
    describeHarvestRead(
      preview({ turns_read: 2, first_turn: 9, last_turn: 10, replies_total: 10, turns_dropped: 3 }),
    ),
    "Read replies 9–10 of 10 — the 3 oldest unread replies were left out for length.",
  );
  assert.equal(
    describeHarvestRead(
      preview({ turns_read: 1, first_turn: 1, last_turn: 1, replies_total: 1, transcript_truncated: true }),
    ),
    "Read reply 1 of 1 — the start of one long reply was left out for length.",
  );
  assert.equal(formatHarvestCost(0.004), "under $0.01");
  assert.equal(formatHarvestCost(0.0412), "≈ $0.04");
  assert.equal(formatHarvestCost(Number.NaN), "");
});

// ---------------------------------------------------------------------------
// The API client
// ---------------------------------------------------------------------------

function stubFetch(
  t: { after: (fn: () => void) => void },
  status: number,
  body: Record<string, unknown>,
) {
  const originalFetch = globalThis.fetch;
  const captured: { input?: RequestInfo | URL; init?: RequestInit } = {};
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async (input, init) => {
    captured.input = input;
    captured.init = init;
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  };
  return captured;
}

test("running the harvest posts the lease and returns the sheet", async (t) => {
  const captured = stubFetch(t, 200, preview({ token: "abc", proposals: [proposal(0)] }));
  const sheet = await runFactHarvest({ workspaceId: 3, generation: 7 });
  assert.equal(captured.input, "/api/project/facts/harvest");
  assert.equal(captured.init?.method, "POST");
  assert.deepEqual(JSON.parse(String(captured.init?.body)), { workspace_id: 3, generation: 7 });
  assert.equal(sheet.token, "abc");
  assert.equal(sheet.proposals.length, 1);
});

test("a refused harvest surfaces the server's code, so the dialog can branch on it", async (t) => {
  stubFetch(t, 502, {
    ok: false,
    code: "harvest_refused",
    error: "The model's safety classifier declined to read this session.",
  });
  await assert.rejects(runFactHarvest(), (error: unknown) => {
    assert.ok(error instanceof HarvestRequestError);
    assert.equal(error.code, "harvest_refused");
    assert.match(error.message, /declined/);
    return true;
  });
});

test("a commit sends only the decision, and a refused proposal comes back by index", async (t) => {
  const captured = stubFetch(t, 400, {
    ok: false,
    code: "invalid_fact",
    error: "1 proposal(s) cannot be recorded as they stand — nothing was recorded.",
    errors: { "2": "source_ref 'ref-9' names no attached document." },
  });
  await assert.rejects(
    commitFactHarvest(
      { token: "tok", accepted: [0, 2], edits: { "2": { source_ref: "ref-9" } } },
      { workspaceId: 1, generation: 2 },
    ),
    (error: unknown) => {
      assert.ok(error instanceof HarvestRequestError);
      assert.equal(error.code, "invalid_fact");
      assert.deepEqual(error.errors, { "2": "source_ref 'ref-9' names no attached document." });
      return true;
    },
  );
  assert.equal(captured.input, "/api/project/facts/harvest/commit");
  assert.deepEqual(JSON.parse(String(captured.init?.body)), {
    token: "tok",
    accepted: [0, 2],
    edits: { "2": { source_ref: "ref-9" } },
    workspace_id: 1,
    generation: 2,
  });
});

// ---------------------------------------------------------------------------
// The wiring (source level)
// ---------------------------------------------------------------------------

test("a commit refreshes the ledger, readiness AND Final QC; a run re-reads the meter", () => {
  const app = read("../src/App.tsx");
  const commit = app.slice(
    app.indexOf("const commitHarvestHandler"),
    app.indexOf("const assistantBubbleCount"),
  );
  assert.match(commit, /commitFactHarvest\(/);
  assert.match(commit, /setProjectFacts\(result\.project_facts\)/);
  assert.match(commit, /setHarvestStatus\(result\.harvest\)/);
  assert.match(commit, /refreshReadiness\(\)/);
  assert.match(commit, /refreshQc\(\)/);
  const run = app.slice(
    app.indexOf("const runHarvestHandler"),
    app.indexOf("const commitHarvestHandler"),
  );
  assert.match(run, /runFactHarvest\(/);
  assert.match(run, /finally\s*{\s*refreshUsage\(\);/, "metered whatever it produced");
  // The hint rides every payload the panel hydrates from.
  assert.match(app, /setHarvestStatus\(payload\.harvest \?\? null\)/);
  assert.match(app, /harvest: merged\.harvest/);
});

test("the dialog runs only when Run is pressed — never on mount", () => {
  const dialog = read("../src/components/HarvestDialog.tsx");
  assert.match(dialog, /data-capability="project\.facts-harvest"/);
  // onRun is called from `run` alone, and `run` only from a click.
  assert.equal(dialog.match(/onRun\(\)/g)?.length, 1);
  assert.match(dialog, /const run = async \(\) => \{[\s\S]*?await onRun\(\)/);
  assert.match(dialog, /onClick=\{\(\) => void run\(\)\}/);
  const effects = dialog.match(/useEffect\(\(\) => \{[\s\S]*?\}, \[[^\]]*\]\);/g) ?? [];
  for (const effect of effects) {
    assert.doesNotMatch(effect, /run\(|onRun|onCommit/, "no effect starts the paid call");
  }
  // The commit sends what buildHarvestCommit decides, nothing assembled inline.
  assert.match(dialog, /buildHarvestCommit\(preview\.token, preview\.proposals, accepted, drafts\)/);
});

test("every door OPENS the dialog; none of them runs the pass", () => {
  const panel = read("../src/components/ProjectFactsPanel.tsx");
  const next = read("../src/components/NextSectionDialog.tsx");
  const artifact = read("../src/components/ArtifactPanel.tsx");
  for (const [name, source] of [
    ["ProjectFactsPanel", panel],
    ["NextSectionDialog", next],
    ["ArtifactPanel", artifact],
  ] as const) {
    assert.doesNotMatch(source, /runFactHarvest|commitFactHarvest/, `${name} never calls the API`);
  }
  // The facts panel: a button declaring the capability that calls onHarvest.
  assert.match(panel, /onClick=\{onHarvest\}\s*data-capability="project\.facts-harvest"/);
  // Next section: "Harvest first" hands up to the panel, which opens the dialog.
  assert.match(next, /onClick=\{onHarvestFirst\}/);
  assert.match(artifact, /onHarvestFirst=\{\(\) => setHarvestOpen\(true\)\}/);
  // The Export menu's hint closes the menu and opens the dialog.
  assert.match(
    artifact,
    /setExportMenuOpen\(false\);\s*setHarvestOpen\(true\);[\s\S]{0,400}data-capability="project\.facts-harvest"/,
  );
  // Not in a tour: the practice copy is not the user's session.
  assert.match(artifact, /harvestAvailable=\{!tutorialActive\}/);
  assert.match(artifact, /\{harvestOpen && !tutorialActive && \(/);
});
