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
