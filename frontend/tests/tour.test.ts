import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

const tour = readFileSync(new URL("../src/lib/tour.ts", import.meta.url), "utf8");
const registry = readFileSync(
  new URL("../src/lib/capabilities.ts", import.meta.url),
  "utf8",
);
const hook = readFileSync(
  new URL("../src/lib/useOnboarding.ts", import.meta.url),
  "utf8",
);
const overlay = readFileSync(
  new URL("../src/components/OnboardingOverlay.tsx", import.meta.url),
  "utf8",
);
const storage = readFileSync(
  new URL("../src/lib/onboardingStorage.ts", import.meta.url),
  "utf8",
);
const dialog = readFileSync(
  new URL("../src/components/NewSessionDialog.tsx", import.meta.url),
  "utf8",
);
const help = readFileSync(
  new URL("../src/components/HelpModal.tsx", import.meta.url),
  "utf8",
);
const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const artifact = readFileSync(
  new URL("../src/components/ArtifactPanel.tsx", import.meta.url),
  "utf8",
);
const specDocument = readFileSync(
  new URL("../src/components/SpecDocument.tsx", import.meta.url),
  "utf8",
);
const settings = readFileSync(
  new URL("../src/components/SettingsPanel.tsx", import.meta.url),
  "utf8",
);
const chat = readFileSync(
  new URL("../src/components/Chat.tsx", import.meta.url),
  "utf8",
);
const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const modalShell = readFileSync(
  new URL("../src/components/ModalShell.tsx", import.meta.url),
  "utf8",
);

const quoted = (text: string) => [...text.matchAll(/"([a-z][a-z0-9.-]+)"/g)].map((m) => m[1]);

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = `${directory}/${name}`;
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(tsx?|jsx?)$/.test(name) ? [path] : [];
  });
}

test("tutorial manifest covers every registered end-user capability", () => {
  const registryBody = /END_USER_CAPABILITIES\s*=\s*\[([\s\S]*?)\]\s*as const/.exec(registry)?.[1];
  assert.ok(registryBody, "capability registry must be a literal typed list");
  const expected = new Set(quoted(registryBody));
  const covered = new Set<string>();
  for (const match of tour.matchAll(/capabilities:\s*\[([^\]]*)\]/g)) {
    for (const id of quoted(match[1])) covered.add(id);
  }
  assert.deepEqual([...covered].sort(), [...expected].sort());
});

test("every registered capability is declared by production UI", () => {
  const registryBody = /END_USER_CAPABILITIES\s*=\s*\[([\s\S]*?)\]\s*as const/.exec(registry)?.[1];
  assert.ok(registryBody);
  const expected = new Set(quoted(registryBody));
  const root = fileURLToPath(new URL("../src/", import.meta.url));
  const productionUi = sourceFiles(root)
    .filter((path) => !path.endsWith("capabilities.ts") && !path.endsWith("tour.ts"))
    .map((path) => readFileSync(path, "utf8"))
    .join("\n");
  const declared = new Set<string>();
  for (const match of productionUi.matchAll(/data-capability\s*=\s*"([^"]+)"/g)) {
    for (const id of match[1].split(/\s+/)) declared.add(id);
  }
  for (const match of productionUi.matchAll(/data-capability\s*=\s*\{([\s\S]*?)\}/g)) {
    for (const id of quoted(match[1])) declared.add(id);
  }
  assert.deepEqual([...declared].sort(), [...expected].sort());
});

test("every step anchor resolves to a real production data-tour attribute", () => {
  // Without this, a renamed or mistyped anchor degrades silently into the
  // "control is not available in the current UI state" card after a ~2s
  // retry loop — which reads as a legitimate product state, not a bug.
  const root = fileURLToPath(new URL("../src/", import.meta.url));
  const productionUi = sourceFiles(root)
    .filter((path) => !path.endsWith("tour.ts"))
    .map((path) => readFileSync(path, "utf8"))
    .join("\n");
  const declared = new Set(
    [...productionUi.matchAll(/data-tour\s*=\s*"([^"${]+)"/g)].map((m) => m[1]),
  );
  const anchors = [...tour.matchAll(/\n\s*anchor:\s*"([^"]*)"/g)].map((m) => m[1]);
  assert.ok(anchors.length > 0, "the manifest must declare anchors");
  const missing = anchors.filter((anchor) => anchor && !declared.has(anchor));
  assert.deepEqual(missing, []);
});

test("the blank-start chapter's controls exist in the empty-workspace renderers", () => {
  // The blank scenario empties BOTH panes, so exactly two renderers are on
  // screen: ArtifactPanel's EmptyState (never SpecDocument) and Chat's
  // empty-state branch (never the message list). A capability declared only
  // in SpecDocument — or only in Chat's populated branch — is unreachable in
  // the very chapter that teaches it, and the step would spotlight nothing
  // and degrade to the "control is not available" card.
  const chapter = /id:\s*"blank-start"[\s\S]*?\n\s{2}\}/.exec(tour)?.[0];
  assert.ok(chapter, "the blank-start chapter must exist");
  assert.match(chapter, /scenario:\s*"blank"/);
  const taught = new Set<string>();
  for (const match of chapter.matchAll(/capabilities:\s*\[([^\]]*)\]/g)) {
    for (const id of quoted(match[1])) taught.add(id);
  }
  assert.ok(taught.size > 0);
  const emptyChat = /messages\.length === 0 \? \(([\s\S]*?)\n\s*\) : \(/.exec(chat)?.[1];
  assert.ok(emptyChat, "Chat must branch on an empty transcript");
  const declared = new Set<string>();
  for (const source of [artifact, emptyChat]) {
    for (const match of source.matchAll(/data-capability\s*=\s*"([^"]+)"/g)) {
      for (const id of match[1].split(/\s+/)) declared.add(id);
    }
  }
  const unreachable = [...taught].filter((id) => !declared.has(id));
  assert.deepEqual(unreachable, []);
  // And #el-sec must exist there, or the section-header resolver cannot bind.
  assert.match(artifact, /id="el-sec"/);
  // The starter chips are the empty chat's own control: anchored where they
  // render, so the step spotlights the chips rather than the whole pane.
  assert.match(emptyChat, /data-tour="starter-prompts"/);
  assert.match(chapter, /anchor:\s*"starter-prompts"/);
});

test("the tour launcher cannot restart the tour from inside the tour", () => {
  // The blank-start chapter spotlights the starter chips, and the overlay
  // deliberately leaves real controls clickable — so the tutorial launcher
  // sits highlighted inside the tour that is running. Starting again from
  // there hits the backend's already-in-a-tutorial conflict, whose recovery
  // path adopts the live tutorial and re-enters at chapter 1, finishing the
  // active scenario and losing the user's place (Codex review, PR #117).
  //
  // Two independent guards, because they fail differently: the ref check
  // makes the action inert wherever it is triggered (the Header's Tour
  // button reaches the same callback), and the disabled chip makes that
  // inertness visible instead of a dead click.
  assert.match(
    hook,
    /if \(workspaceRef\.current\) return;\s*\n[\s\S]{0,400}?void beginShowcase\(\);\s*\n\s*\}, \[beginShowcase\]\);/,
  );
  assert.match(chat, /tourActive\?: boolean/);
  assert.match(chat, /disabled=\{busy \|\| tourActive\}/);
  assert.match(app, /tourActive=\{onboarding\.phase\.kind !== "idle"\}/);
  // A disabled control must not keep inviting the click it will refuse.
  assert.match(chat, /tourActive \|\| \(toured && !tutorialUpdated\)/);
});

test("the starter chips send nothing while the tutorial is running", () => {
  // The blank-start chapter spotlights all five chips on an empty practice
  // page, and the four chat ones each send a real billed turn — into the
  // fixture the very next step teaches the user to fill in themselves. The
  // tour is a fixed track the user only watches, so recognizing the on-ramps
  // is the lesson and spending money on one is not.
  const chatChip =
    /onClick=\{\(\) => onSend\(p\.label\)\}([\s\S]{0,200}?)className=/.exec(chat)?.[1];
  assert.ok(chatChip, "the chat starter chip must send its own label");
  assert.match(chatChip, /disabled=\{busy \|\| tourActive\}/);
  // And it says why, rather than reading as a dead control.
  assert.match(chat, /tourActive \? "Available once the tutorial ends"/);
  // The step's copy must not promise a click the tour withholds.
  const step = /id:\s*"starter-paths"[\s\S]*?\n\s{6}\}/.exec(tour)?.[0];
  assert.ok(step, "the starter-prompts step must exist");
  assert.match(step, /held inert/);
});

test("a step with no anchor supplies a document resolver instead", () => {
  const steps = [...tour.matchAll(/\{\s*\n\s*id:\s*"[^"]+",[\s\S]*?\n\s{6}\}/g)].map(
    (m) => m[0],
  );
  const anchorless = steps.filter((step) => /anchor:\s*""/.test(step));
  assert.ok(anchorless.length > 0, "blank anchors exist only with a resolver");
  for (const step of anchorless) assert.match(step, /resolve:\s*"/);
});

test("tutorial is versioned, resumable, and document-aware", () => {
  assert.match(tour, /TOUR_VERSION\s*=\s*\d+/);
  assert.match(tour, /mode:\s*"optional"/);
  assert.match(tour, /mode:\s*"explanatory"/);
  assert.match(tour, /resolve:\s*"reorderable-paragraph"/);
  assert.match(tour, /first-imported/);
  assert.match(tour, /scenario:\s*"references"/);
  assert.match(tour, /scenario:\s*"import"/);
  assert.match(tour, /scenario:\s*"qc"/);
  assert.match(hook, /loadOnboardingProgress\(TOUR_VERSION\)/);
  assert.match(hook, /getTutorialStatus/);
  assert.match(storage, /build-a-spec:onboarding-progress/);
  assert.match(storage, /generation:\s*number/);
  assert.match(hook, /status\.workspace_id !== stored\.workspaceId/);
  assert.match(hook, /status\.generation !== stored\.generation/);
  assert.match(storage, /ONBOARDING_COMPLETION_VERSION\s*=\s*2/);
  assert.match(storage, /consumeTutorialUpdateInvitation/);
  assert.match(chat, /Full tutorial updated/);
  assert.doesNotMatch(chat, /passive|3-minute|~3 minutes/i);
});

test("review tutorial does not offer a one-click confirmation shortcut", () => {
  assert.doesNotMatch(tour, /confirm-first|Confirm the first outstanding block/);
  assert.doesNotMatch(overlay, /confirm-first/);
  assert.doesNotMatch(hook, /confirm-first/);
});

test("workspace lifecycle protects current work and always restores it", () => {
  assert.match(hook, /startTutorialWorkspace/);
  assert.match(hook, /startTutorialScenario/);
  assert.match(hook, /finishTutorialScenario/);
  assert.match(hook, /restoreTutorialWorkspace/);
  assert.match(tour, /id:\s*"paper"[\s\S]*?scenario:\s*"structural"/);
});

test("repeated starts join one in-flight backend transition", () => {
  // A double-clicked Tour button must not mint a second idempotency key or
  // orphan the first start into restoring the workspace its successor just
  // adopted (Codex review on PR #113). The in-flight guard makes the second
  // click a join, and the pending request id survives until a start
  // succeeds — starting again never resets it.
  assert.match(hook, /startInFlightRef/);
  assert.match(hook, /if \(startInFlightRef\.current\) return;/);
  assert.match(hook, /startRequestRef\.current \?\? requestId\(\)/);
  assert.doesNotMatch(
    hook,
    /const start = useCallback[\s\S]{0,500}?startRequestRef\.current = null/,
  );
});

test("the bundled showcase is the tutorial's only source", () => {
  // No chooser modal, no current-project copy, no live generation: Start
  // goes straight to the protected showcase workspace.
  assert.doesNotMatch(overlay, /Use my current spec/);
  assert.doesNotMatch(overlay, /Generate a tutorial spec with AI/);
  assert.doesNotMatch(overlay, /Use bundled showcase/);
  assert.doesNotMatch(hook, /source-choice|enrichment-choice/);
  assert.doesNotMatch(hook, /chooseSource|chooseLiveEnrichment|chooseBundledEnrichment/);
  assert.match(hook, /beginShowcase/);
  assert.match(api, /source:\s*"showcase"/);
  // The manifest's own copy must not resurrect the choice.
  assert.doesNotMatch(tour, /you selected|AI-generated spec/);
  assert.match(tour, /bundled showcase spec/);
});

test("the tutorial has exactly one ending, and it is a restore", () => {
  // Nothing may reintroduce a second disposition or a modal that asks which
  // one the user wants. Ending returns the pre-tutorial project, always.
  assert.doesNotMatch(hook, /keepTutorialWorkspace|downloadOriginalProjectCopy/);
  assert.doesNotMatch(hook, /kind:\s*"completion"/);
  assert.doesNotMatch(hook, /chooseKeep|saveOriginalAndKeep|saveCopyAndRestore/);
  assert.doesNotMatch(api, /tutorial\/keep|scope=original/);
  assert.doesNotMatch(overlay, /What should happen to the tutorial workspace/);
  assert.doesNotMatch(overlay, /Save tutorial copy, then return/);
  assert.doesNotMatch(overlay, /Replace my project with tutorial copy/);
  assert.doesNotMatch(overlay, /Replace the original working project/);
  assert.doesNotMatch(overlay, /keepConfirm/);

  // Progress and failure both ride the existing preparing/finishing stage,
  // so there is no dedicated end-of-tour surface to grow options back onto.
  assert.match(hook, /stage:\s*"finishing"/);
  assert.match(tour, /continueLabel:\s*"Finish and return to my project"/);

  // Every trigger shares one implementation. Only the cosmetic completion
  // flag varies, so there is exactly one place it can be set.
  assert.match(hook, /restoreOriginal/);
  assert.equal([...hook.matchAll(/markOnboardingCompleted\(\)/g)].length, 1);
  assert.match(hook, /abort[\s\S]{0,160}restoreOriginal\(\{\s*completed:\s*false/);

  // A failed restore retries the restore. Falling through to chooseSource
  // would restart the whole tutorial on the way out of it.
  assert.match(hook, /current\.stage === "finishing"[\s\S]{0,200}restoreOriginal/);

  // Restores are serialized, so a double-clicked End cannot fire a second
  // request that 409s against the workspace the first one just finished.
  assert.match(hook, /restoreInFlightRef/);

  // Finding the tutorial already gone still has to rehydrate from an
  // authoritative payload: /api/tutorial/status omits `session` once the
  // tutorial is over, so settling from it would close the overlay while the
  // panel still rendered the discarded practice copy.
  assert.match(hook, /const restored = await getSessionBundle\(\)/);
  assert.match(hook, /settle\(restored\)/);
  assert.doesNotMatch(hook, /settle\(status\.session/);

  // Every settle that follows an await is generation-guarded. acceptNativeRestore
  // is not a restore call, so it bypasses the in-flight serialization and can
  // apply newer state mid-fetch; an unguarded settle would overwrite it.
  assert.match(hook, /if \(runRef\.current !== run\) return true;\s*\n\s*settle\(restored\)/);

  // The capability moved to the real End controls, not a modal wrapper.
  assert.match(overlay, /data-capability="tour\.finish"/);
  assert.doesNotMatch(overlay, /<div data-capability="tour\.finish">/);

  // The one surviving prompt must not advertise a choice that is gone.
  assert.match(app, /End the guided tour\?/);
  assert.doesNotMatch(app, /choose what to keep/i);
});

test("the tutorial runs start to finish and cannot be suspended", () => {
  // A guided tour has two states — running, or ended and the project back.
  // A third "paused" state let the tour sit indefinitely holding the user's
  // real session in a protected workspace, with a pill as the only reminder.
  assert.doesNotMatch(hook, /kind:\s*"paused"/);
  assert.doesNotMatch(hook, /\bpause:\s*\(\)|\bresume:\s*\(\)|askQuestion/);
  assert.doesNotMatch(overlay, /ob\.pause|ob\.resume|ob\.askQuestion/);
  assert.doesNotMatch(overlay, /Resume tutorial/);
  // Nothing persists a suspended flag, so no stored record can restore one.
  assert.doesNotMatch(storage, /paused/);

  // A reload re-enters the tour at the stored step instead of parking it, and
  // goes through enterChunk so a chapter whose scenario the server no longer
  // holds gets it swapped back in.
  assert.match(
    hook,
    /enterChunkRef\.current\?\.\([\s\S]{0,120}Math\.min\(stored\.step/,
  );

  // Escape asks to end, the same as every ✕ and backdrop click in the tour.
  assert.match(
    overlay,
    /event\.key !== "Escape"[\s\S]{0,220}ob\.requestEnd\(\)/,
  );

  // And the manifest may not promise an affordance that is gone.
  assert.doesNotMatch(tour, /\bpause\b/i);
});

test("every tutorial surface offers a way back to the user's project", () => {
  // The tour holds the real session aside in a protected workspace, so a
  // surface a user can reach but not leave strands their project behind a
  // modal. Every phase the overlay renders is checked here.
  const branch = (start: string, end: string) => {
    const from = overlay.indexOf(start);
    assert.ok(from >= 0, `overlay branch not found: ${start}`);
    const to = overlay.indexOf(end, from + start.length);
    assert.ok(to > from, `overlay branch has no end: ${end}`);
    return overlay.slice(from, to);
  };
  const preparing = branch(
    'if (phase.kind === "preparing") {',
    'if (phase.kind === "chunk-break") {',
  );
  const checkpoint = branch(
    'if (phase.kind === "chunk-break") {',
    "const chapter = TOUR[phase.chunk];",
  );
  const touring = branch("const chapter = TOUR[phase.chunk];", "function CardHeader(");

  // Waiting on a start or a scenario swap: an explicit End beside the bar.
  // Waiting on the RESTORE is the one exception — that already IS the ending,
  // so it offers nothing to leave to, which is why the guard is on `finishing`.
  assert.match(preparing, /\{!finishing && \([\s\S]{0,400}?onClick=\{ob\.requestEnd\}/);
  // A failure leaves both doors open: retry, or go the other way.
  assert.match(preparing, /onClick=\{finishing \? ob\.stayInTutorial : ob\.requestEnd\}/);
  // The between-chapters checkpoint is modal and blocks the app behind it.
  assert.match(checkpoint, /ob\.continueChunk[\s\S]{0,220}?onClick=\{ob\.requestEnd\}/);
  // The step card: the ✕ in its header and the End button in its footer.
  assert.match(touring, /onClose=\{ob\.requestEnd\}/);
  assert.match(touring, /ob\.advance[\s\S]{0,600}?onClick=\{ob\.requestEnd\}/);

  // Each of those three is a labelled control, not just a ✕ glyph, and each
  // declares the capability so the coverage contract sees it.
  assert.equal(
    [...overlay.matchAll(/data-capability="tour\.finish"/g)].length,
    3,
    "one End control per reachable tutorial surface",
  );
  // The ✕ that closes a ModalShell must route to the confirmation too — a
  // backdrop click is the easiest way to hit one by accident.
  assert.match(overlay, /aria-label="End tutorial"/);
  assert.doesNotMatch(overlay, /onClose=\{\(\) => setPhase/);
});

test("a modal stacked over the tour owns Escape", () => {
  // The step card is non-blocking, so the template studio the template-create
  // step opens (and help, and a stop confirmation) renders above a tour that
  // stays in `touring`. Those listen for Escape on `window` exactly as the
  // tour does, so one keypress reached both handlers and dismissing the studio
  // also prompted to end the tutorial (Codex review, PR #120). Pause used to
  // hide this: opening the studio suspended the tour.
  //
  // Two independent guards, because they cover different dialogs.
  assert.match(overlay, /event\.defaultPrevented/);
  assert.match(overlay, /\[role="dialog"\]\[aria-modal="true"\]/);
  assert.match(overlay, /anotherDialogOwnsEscape\(\)\) return;/);
  // The guard must run BEFORE the phase check, or it guards nothing.
  assert.match(
    overlay,
    /anotherDialogOwnsEscape\(\)\) return;[\s\S]{0,120}phase\.kind === "touring"/,
  );

  // It skips the tour's OWN modals by marker, so Escape still works on the
  // checkpoint and the preparing card — both are stamped, and the step card
  // is aria-modal="false" so the selector never matches it in the first place.
  assert.equal([...overlay.matchAll(/marker=\{TOUR_DIALOG\}/g)].length, 2);
  assert.match(modalShell, /data-dialog=\{marker\}/);
  assert.match(overlay, /aria-modal="false"/);
});

test("the tutorial is a fixed track the user only watches", () => {
  // The tour holds the user's real session aside in a protected workspace and
  // walks the whole app in order. It hands out no controls of its own and
  // waits on nothing: a reader who only ever presses Continue sees every
  // chapter. Three mechanisms used to break that, and each is pinned gone.

  // 1. Step action buttons ("Try a guided answer" prefilled the composer,
  //    "Fill the showcase profile" wrote a profile edit into the tutorial
  //    document, "Open template studio" opened a real modal).
  assert.doesNotMatch(tour, /\n\s*actions:/);
  assert.doesNotMatch(tour, /TourAction/);
  assert.doesNotMatch(tour, /prefillText/);
  assert.doesNotMatch(hook, /runStepAction|prefillComposer|openTemplates/);
  assert.doesNotMatch(overlay, /runStepAction|StepActions/);
  // The hook asks the host for workspace lifecycle only — an action cap is
  // how a tour-driven mutation would get back in.
  assert.doesNotMatch(hook, /editDoc|startResearch|startQc/);

  // 2. The rearrange step disabled Continue until the user physically moved a
  //    block on the paper, so the tour could not be completed by watching.
  assert.doesNotMatch(overlay, /documentOrderSignature|rearrangeComplete/);
  assert.doesNotMatch(overlay, /Move one article or paragraph now/);
  // Only an in-flight turn may hold Continue, because `advance` can swap in
  // the next chapter's scenario. Nothing about the step may gate it.
  assert.match(overlay, /onClick=\{ob\.advance\} disabled=\{busy\}/);
  assert.doesNotMatch(overlay, /disabled=\{busy \|\| /);

  // 3. The mode badge may not advertise interactivity the tour no longer has.
  //    "optional" survives — it marks a step whose SUBJECT costs money to run.
  assert.doesNotMatch(tour, /mode:\s*"interactive"/);

  // The structural chapter still exists; it is now shown, not assigned.
  assert.match(tour, /scenario:\s*"structural"/);
  assert.match(tour, /disposable practice state/);

  // 4. The tour does not point the reader at the chat. "Ask a question" was a
  //    checkpoint button that went with pause, and the copy that replaced it
  //    kept issuing the same invitation — which is asking something of a
  //    reader who is only meant to watch.
  assert.doesNotMatch(tour, /\bask\s+(a|another|the)\s+question/i);
  assert.doesNotMatch(overlay, /\bask\s+(a|another|the)\s+question/i);

  // The spotlight still leaves the real app clickable — passive means the tour
  // asks nothing, not that the app is locked. That is what keeps the stacked
  // -modal Escape guard load-bearing (see the test below).
  assert.match(overlay, /pointer-events-none fixed inset-0/);
});

test("spotlight leaves real controls interactive and missing fixtures degrade honestly", () => {
  assert.match(overlay, /pointer-events-none fixed inset-0/);
  assert.match(overlay, /pointer-events-auto fixed z-\[65\]/);
  assert.match(overlay, /readinessMet/);
  assert.match(overlay, /data-tour=\\?"doc-panel/);
  assert.doesNotMatch(overlay, /left-1\/2 top-1\/3/);
  // The repair path went with the enrichment surface: a missing fixture is
  // explained beside the real document, never rebuilt with a model call.
  assert.doesNotMatch(overlay, /Build the missing example/);
  assert.doesNotMatch(overlay, /ensureContent/);
  assert.match(overlay, /could not be prepared\. You can continue/);
});

test("paid research and QC results are truthful optional skips, never generic enrichment", () => {
  assert.match(overlay, /There is no completed research result/);
  assert.match(overlay, /There is no completed Final QC result/);
  assert.match(overlay, /no findings or readiness state will be fabricated/);
  // The enrichment machinery is gone entirely, so no readiness gap can
  // trigger a model call from inside the tour.
  assert.doesNotMatch(hook, /enrich/i);
  assert.doesNotMatch(overlay, /enrich/i);
});

test("template paths are active and cover create, preview, import, manage, and start", () => {
  assert.doesNotMatch(dialog, /Template features are in development/i);
  assert.match(dialog, /Create from current spec/);
  assert.match(dialog, /Preview template/);
  assert.match(dialog, /Save reusable template/);
  assert.match(dialog, /What AI generalization changed/);
  assert.match(dialog, /generic fallback/);
  assert.match(dialog, /Load your own template file/);
  assert.match(dialog, /Export template/);
  assert.match(dialog, /Confirm delete/);
  assert.match(dialog, /Filter templates/);
  assert.match(dialog, /template\.project_type/);
  assert.match(dialog, /No templates match/);
  assert.match(dialog, /application\/vnd\.buildaspec\.template\+json/);
  assert.doesNotMatch(dialog, /application\/zip/);
  assert.match(dialog, /onStartTemplate\(template\.id\)/);
  assert.match(dialog, /event\.key === "Escape"/);
  assert.match(artifact, /Save as Template/);
  assert.match(artifact, /data-tour="save-template"/);
  assert.match(settings, /template:\s*"Template creation"/);
  assert.match(specDocument, /template starter/);
  assert.match(specDocument, /seed_block_ids/);
});

test("help can restart the full tutorial or jump to any named chapter", () => {
  assert.match(help, /Restart full tutorial/);
  assert.match(help, /TOUR\.map/);
  assert.match(help, /onStartTutorialAtChapter\(chapter\.id\)/);
  assert.match(hook, /startAtChapter/);
  assert.match(hook, /pendingStartChunkRef/);
  assert.match(hook, /syncSessionIdentity/);
  assert.match(app, /onboardingRef\.current\?\.syncSessionIdentity\(session\)/);
  assert.match(app, /onboarding\.startAtChapter\(chapterId\)/);
});

test("the enrichment surface is gone with the source choice", () => {
  // The showcase satisfies coverage by construction (pinned in the backend
  // suite), so no route, stream, or UI may reintroduce an enrichment pass.
  assert.doesNotMatch(api, /tutorial\/enrich/);
  assert.doesNotMatch(api, /mode:\s*args\.mode/);
  assert.doesNotMatch(hook, /tutorial_fallback|tutorial_session|tutorial_coverage/);
  assert.match(api, /workspace_id:\s*args\.workspaceId/);
  assert.match(api, /generation:\s*args\.generation/);
});
