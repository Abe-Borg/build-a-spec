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

test("the blank-start chapter's controls exist in the empty-document renderer", () => {
  // A blank document renders ArtifactPanel's EmptyState, NOT SpecDocument, so
  // a capability declared only in SpecDocument is unreachable in the very
  // chapter that teaches it — the step would spotlight nothing and degrade to
  // the "control is not available" card.
  const chapter = /id:\s*"blank-start"[\s\S]*?\n\s{2}\}/.exec(tour)?.[0];
  assert.ok(chapter, "the blank-start chapter must exist");
  assert.match(chapter, /scenario:\s*"blank"/);
  const taught = new Set<string>();
  for (const match of chapter.matchAll(/capabilities:\s*\[([^\]]*)\]/g)) {
    for (const id of quoted(match[1])) taught.add(id);
  }
  assert.ok(taught.size > 0);
  const declared = new Set<string>();
  for (const match of artifact.matchAll(/data-capability\s*=\s*"([^"]+)"/g)) {
    for (const id of match[1].split(/\s+/)) declared.add(id);
  }
  const unreachable = [...taught].filter((id) => !declared.has(id));
  assert.deepEqual(unreachable, []);
  // And #el-sec must exist there, or the section-header resolver cannot bind.
  assert.match(artifact, /id="el-sec"/);
});

test("a step with no anchor supplies a document resolver instead", () => {
  const steps = [...tour.matchAll(/\{\s*\n\s*id:\s*"[^"]+",[\s\S]*?\n\s{6}\}/g)].map(
    (m) => m[0],
  );
  const anchorless = steps.filter((step) => /anchor:\s*""/.test(step));
  assert.ok(anchorless.length > 0, "blank anchors exist only with a resolver");
  for (const step of anchorless) assert.match(step, /resolve:\s*"/);
});

test("tutorial is versioned, resumable, interactive, and document-aware", () => {
  assert.match(tour, /TOUR_VERSION\s*=\s*\d+/);
  assert.match(tour, /mode:\s*"interactive"/);
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
  assert.match(hook, /enrichTutorialWorkspace/);
  assert.match(hook, /startTutorialScenario/);
  assert.match(hook, /finishTutorialScenario/);
  assert.match(hook, /restoreTutorialWorkspace/);
  assert.match(overlay, /Use my current spec/);
  assert.match(overlay, /Generate a tutorial spec with AI/);
  assert.match(overlay, /Use bundled showcase/);
  assert.match(tour, /id:\s*"paper"[\s\S]*?scenario:\s*"structural"/);
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

  // The capability moved to the real End controls, not a modal wrapper.
  assert.match(overlay, /data-capability="tour\.finish"/);
  assert.doesNotMatch(overlay, /<div data-capability="tour\.finish">/);

  // The one surviving prompt must not advertise a choice that is gone.
  assert.match(app, /End the guided tour\?/);
  assert.doesNotMatch(app, /choose what to keep/i);
});

test("rearrangement is a required real-document exercise", () => {
  assert.match(overlay, /documentOrderSignature/);
  assert.match(overlay, /paragraphs\.map\(\(item\) => item\.id\)/);
  assert.match(overlay, /Move one article or paragraph now/);
  assert.match(overlay, /stable ID and complete/);
  assert.match(
    overlay,
    /disabled=\{busy \|\| \(step\.id === "rearrange" && !rearrangeComplete\)\}/,
  );
  assert.match(tour, /scenario:\s*"structural"/);
  assert.match(tour, /disposable practice state/);
});

test("spotlight leaves real controls interactive and missing fixtures are repairable", () => {
  assert.match(overlay, /pointer-events-none fixed inset-0/);
  assert.match(overlay, /pointer-events-auto fixed z-\[65\]/);
  assert.match(overlay, /readinessMet/);
  assert.match(overlay, /data-tour=\\?"doc-panel/);
  assert.doesNotMatch(overlay, /left-1\/2 top-1\/3/);
  assert.match(overlay, /Build the missing example/);
  assert.match(overlay, /ob\.ensureContent/);
});

test("paid research and QC results are truthful optional skips, never generic enrichment", () => {
  assert.match(overlay, /There is no completed research result/);
  assert.match(overlay, /There is no completed Final QC result/);
  assert.match(overlay, /no findings or readiness state will be fabricated/);
  assert.match(hook, /readiness === "content"[\s\S]*?readiness === "rich-structure"[\s\S]*?readiness === "versioned"/);
  assert.doesNotMatch(hook, /readiness === "research"[\s\S]{0,120}?void enrich/);
  assert.doesNotMatch(hook, /readiness === "qc"[\s\S]{0,120}?void enrich/);
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

test("tutorial streams reject stale workspace generations and only explicit fallback replaces state", () => {
  assert.match(hook, /event\.type === "tutorial_fallback"/);
  assert.match(hook, /event\.replaces_workspace_id !== workspace\.workspaceId/);
  assert.match(hook, /event\.replaces_generation !== workspace\.generation/);
  assert.match(hook, /event\.workspace_id !== workspace\.workspaceId/);
  assert.match(hook, /event\.generation !== workspace\.generation/);
  assert.match(api, /workspace_id:\s*args\.workspaceId/);
  assert.match(api, /generation:\s*args\.generation/);
  assert.match(overlay, /Enrich the copy with AI/);
  assert.match(overlay, /Add bundled showcase examples/);
  assert.match(overlay, /real billed model usage/);
  assert.match(api, /mode:\s*args\.mode \?\? "live"/);
});
