import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Final QC's adjudication rule is final-qc/4 (backend/qc/engine.py
// `panel_outcome`): every seat upholds → upheld; refuters outnumber upholders
// → refuted; anything else → disputed. The v3 rule it replaced ("majority,
// tie to the refuters") outlived the code in three user-facing copy sites,
// and every one of them also quotes the panel sizes. This test reads the
// sizes off `backend/settings.py` so a change to a panel size fails the copy
// that quotes it — the trust dossier's own "every number is real" contract.

const settings = readFileSync(
  new URL("../../backend/settings.py", import.meta.url),
  "utf8",
);
const help = readFileSync(
  new URL("../src/components/HelpModal.tsx", import.meta.url),
  "utf8",
);
const dossier = readFileSync(
  new URL("../src/components/TrustDeepDiveModal.tsx", import.meta.url),
  "utf8",
);
const report = readFileSync(
  new URL("../src/components/QCReportModal.tsx", import.meta.url),
  "utf8",
);

const NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six"];

function panelDefault(name: string): string {
  const match = settings.match(
    new RegExp(`^${name}\\s*=\\s*_int_env\\("[A-Z_]+",\\s*(\\d+)`, "m"),
  );
  assert.ok(match, `${name} default not found in backend/settings.py`);
  const word = NUMBER_WORDS[Number(match[1])];
  assert.ok(word, `${name} default ${match[1]} has no number word`);
  return word;
}

const critical = panelDefault("QC_VERIFIERS_CRITICAL");
const standard = panelDefault("QC_VERIFIERS_STANDARD");

// JSX wraps prose across lines; compare on folded whitespace.
const fold = (text: string) => text.replace(/\s+/g, " ");

test("warm-lead copy states the shipped eight-seat minimum and scoped switch-off", () => {
  const engine = readFileSync(new URL("../../backend/qc/engine.py", import.meta.url), "utf8");
  const readme = fold(readFileSync(new URL("../../README.md", import.meta.url), "utf8"));
  assert.match(engine, /^_WARM_LEAD_MIN_SEATS_WEB = 8$/m);
  assert.match(engine, /^_WARM_LEAD_MIN_SEATS_NO_WEB = 8$/m);
  assert.match(fold(dossier), /When eight or more seats read the same copy/);
  assert.match(fold(dossier), /8–19 seats or 20 or more.*other groups keep their leads/);
  assert.match(readme, /groups has at least 8 reviewers/);
  assert.match(readme, /Other cohorts keep their leads/);
});

test("research copy states the engine's fixed per-request web allowance and its overshoot", () => {
  // backend/research/engine.py: every request of a research area declares
  // the same allowance (so its cached prefix never changes) and the budgets
  // are checked between requests, so the crossing request can overshoot by
  // its allowance less one. The dossier and README quote both numbers.
  const engine = readFileSync(new URL("../../backend/research/engine.py", import.meta.url), "utf8");
  const readme = fold(readFileSync(new URL("../../README.md", import.meta.url), "utf8"));
  const searches = engine.match(/^RESEARCH_SEARCHES_PER_REQUEST = (\d+)$/m);
  const fetches = engine.match(/^RESEARCH_FETCHES_PER_REQUEST = (\d+)$/m);
  assert.ok(searches && fetches, "per-request allowance not found in backend/research/engine.py");
  const s = Number(searches[1]);
  const f = Number(fetches[1]);
  for (const text of [fold(dossier), readme]) {
    assert.match(text, new RegExp(`at most ${s} searches and ${f} fetches`));
    assert.match(text, new RegExp(`up to ${s - 1} searches or ${f - 1} fetches past it`));
  }
  assert.match(readme, new RegExp(`at most ${s} searches and ${f} page reads`));
  assert.match(
    readme,
    new RegExp(`at most ${s - 1} searches past the 2× search ceiling or ${f - 1} page reads`),
  );
});

test("research launch copy quotes the shipped warm-wait bound and its own switch", () => {
  // backend/settings.py: RESEARCH_WARM_WAIT_SECONDS is the longest a
  // research round's waiting areas hold for the lead's first output. The
  // dossier and README quote the number; the README names the switch.
  const match = settings.match(
    /^RESEARCH_WARM_WAIT_SECONDS = _int_env\(\s*"BUILD_A_SPEC_RESEARCH_WARM_WAIT_SECONDS",\s*(\d+)/m,
  );
  assert.ok(match, "RESEARCH_WARM_WAIT_SECONDS default not found in backend/settings.py");
  const seconds = Number(match[1]);
  const readme = fold(readFileSync(new URL("../../README.md", import.meta.url), "utf8"));
  assert.match(
    fold(dossier),
    new RegExp(`others wait until it begins answering \\(after at most ${seconds} seconds`),
  );
  assert.match(readme, new RegExp(`BUILD_A_SPEC_RESEARCH_WARM_WAIT_SECONDS\` \\(${seconds} s by default\\)`));
  assert.match(readme, new RegExp(`at most ${seconds} s per round`));
  assert.match(readme, /BUILD_A_SPEC_RESEARCH_WARM_WAIT_SECONDS` \| `\d+` \| Staggered research launch/);
});

const SITES: Array<[string, string]> = [
  ["HelpModal", help],
  ["TrustDeepDiveModal", dossier],
  ["QCReportModal", report],
];

// Help and the dossier describe the APP as shipped, so they quote the
// configured defaults. The report modal describes ONE RUN, whose panel sizes
// are whatever `BUILD_A_SPEC_QC_VERIFIERS_*` said at the time — it must
// derive them from the record (Codex, PR #159), never state the defaults.
const APP_DESCRIPTION_SITES = SITES.filter(([name]) => name !== "QCReportModal");

// The one sentence the Word memo's methodology and the report modal's
// methodology both state verbatim (Chunk 5.3: the two projections must never
// teach different meanings). tests/test_qc_audit_report.py pins the same
// literal against the RENDERED memo and against this file.
export const V4_RULE_SENTENCE =
  "A finding survives only when every seat upholds it, is refuted when the " +
  "refuting seats outnumber the upholding ones, and is disputed otherwise; " +
  "a critical or high refutation additionally needs at least one validated " +
  "citation.";

test("the app-description sites quote the panel sizes settings.py actually configures", () => {
  for (const [name, source] of APP_DESCRIPTION_SITES) {
    const text = fold(source);
    assert.match(
      text,
      new RegExp(`\\b${critical} for critical and high\\b`),
      `${name} must name the critical/high panel size (${critical})`,
    );
    assert.match(
      text,
      new RegExp(`\\b${standard} for medium and low\\b`),
      `${name} must name the medium/low panel size (${standard})`,
    );
  }
});

test("the report modal derives the panel sizes from the run it describes", () => {
  assert.match(
    report,
    /qcPanelSizePhrase\(report\)/,
    "QCReportModal's methodology must read the run's panel sizes off the record",
  );
  assert.doesNotMatch(
    fold(report),
    /\b(one|two|three|four|five) for (critical and high|medium and low)\b/,
    "QCReportModal must not hard-code panel sizes — a run under an override would be misdescribed",
  );
});

test("every copy site states the final-qc/4 outcomes and none the retired majority rule", () => {
  for (const [name, source] of SITES) {
    const text = fold(source);
    assert.match(text, /\bdisputed\b/, `${name} must name the disputed outcome`);
    assert.match(
      text,
      /every seat upholds/,
      `${name} must state that survival needs a unanimous panel`,
    );
    // A refuting majority does NOT always refute: a critical/high refutation
    // with no validated citation is disputed (the v4 evidence gate). A card
    // that omits the exception teaches "refuted" where the report says
    // "escalated to you" (Codex, PR #159).
    assert.match(
      text,
      /validated citation/i,
      `${name} must state the critical/high evidence gate`,
    );
    assert.doesNotMatch(
      text,
      /tie goes|panel'?s majority|surviving three refuters/i,
      `${name} still states the retired v3 rule`,
    );
  }
});

test("the report modal's methodology states the memo's rule sentence verbatim", () => {
  assert.ok(
    report.includes(V4_RULE_SENTENCE),
    "QCReportModal's methodology must carry the same rule sentence the Word memo renders",
  );
});
