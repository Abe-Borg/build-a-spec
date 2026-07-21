# Kickoff prompt for Claude Code batch sessions

Paste everything inside the fence below as the first message of a fresh
Claude Code session in the repo root. It works unmodified for every
batch: it detects the next batch on its own. To force a specific batch,
add one line at the bottom: `Implement Batch N.`

```text
Read these files completely, in this order, before touching any code:

1. docs/plans/README.md — the plan index and ground rules. Binding.
2. CLAUDE.md — the engineering reference: conventions, invariants, and
   frozen decisions. Also binding.
3. The plan file for the batch you are implementing.

Which batch: implement the lowest-numbered batch still marked "planned"
in the docs/plans/README.md table (equivalently: the first plan file
with no "## As built" section at its top). Cross-check against VERSION
in backend/settings.py — 0.6.0 means Batch 2 is next, 0.7.0 → Batch 3,
0.8.0 → Batch 4, 0.9.0 → Batch 5. If I explicitly name a batch in this
message, that wins. Implement exactly ONE batch in this session; do not
start the next one.

How to work:

- The plans encode design decisions already made with me. Do not
  relitigate anything the plan or CLAUDE.md marks frozen. If the code
  has drifted from the plan, follow the code's invariants, note the
  deviation for the as-built section, and keep going — but if the
  conflict touches a frozen decision, stop and ask me first.
- Every "VERIFY:" marker in a plan means: check that fact against live
  documentation (web search) before wiring it. Never code from the
  plan's snapshot of an API shape or a price.
- Work the plan's work items in order. Keep the test suite green as you
  go. Tests are hermetic — no network, no real API key; new streaming or
  event behavior means extending tests/fakes.py, never skipping a test.
- Two product mandates apply to everything you build: (1) no quality
  limits on the model — never add token budgets, truncation, or
  cost-saving degradations; only runaway circuit breakers sized so no
  legitimate operation hits them; (2) no dead air in the UI — any
  long-running operation must show live status/progress using the
  status machinery (Batch 2 builds it; later batches reuse it).

Before you declare the batch done — all of this is part of the batch,
not optional cleanup:

1. Full suite green: python -m pytest -q (use the repo's virtualenv).
   No new skips or xfails.
2. Frontend builds clean: cd frontend && npm run build (tsc runs inside
   it).
3. Version bumped in BOTH backend/settings.py (VERSION) and
   frontend/package.json to this batch's version from the plans index —
   test_version_consistency_gate enforces they agree.
4. Living docs updated to match what you ACTUALLY built:
   - README.md: current-status section for the new version; config
     table rows for any new env vars.
   - CLAUDE.md: the SSE/event protocol table if events changed, the
     conversation-engine invariants if touched, and a new "implemented
     notes" section for this batch in the style of the existing ones.
   - requirements.txt if Python dependencies changed.
5. Plan annotated as-built: add an "## As built (<today's date>)"
   section at the TOP of this batch's plan file — status, what shipped,
   and every deviation from the plan with one line of why (mirror
   ROADMAP.md's as-built style). Flip this batch's Status to "shipped"
   in the docs/plans/README.md table.
6. Commit the work with a sassy, spicy commit message — attitude and
   humor where earned, especially if something fought you; never
   obnoxious. That is a standing owner preference, not a joke.

Then stop and give me: a summary of what shipped, the deviations list,
and — explicitly — every manual QA step the plan calls for that I need
to perform myself (streaming feel, Word round-trips, keyboard flows),
as a checklist I can run through.
```
