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

// The one sentence the Word memo's methodology and the report modal's
// methodology both state verbatim (Chunk 5.3: the two projections must never
// teach different meanings). tests/test_qc_audit_report.py pins the same
// literal against the RENDERED memo and against this file.
export const V4_RULE_SENTENCE =
  "A finding survives only when every seat upholds it, is refuted when the " +
  "refuting seats outnumber the upholding ones, and is disputed otherwise; " +
  "a critical or high refutation additionally needs at least one validated " +
  "citation.";

test("every copy site quotes the panel sizes settings.py actually configures", () => {
  for (const [name, source] of SITES) {
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

test("every copy site states the final-qc/4 outcomes and none the retired majority rule", () => {
  for (const [name, source] of SITES) {
    const text = fold(source);
    assert.match(text, /\bdisputed\b/, `${name} must name the disputed outcome`);
    assert.match(
      text,
      /every seat upholds/,
      `${name} must state that survival needs a unanimous panel`,
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
