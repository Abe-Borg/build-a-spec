/**
 * One Escape mechanism for every dialog — pinned at the source level, because
 * the components have no DOM harness (the strandedState.test.ts idiom).
 *
 * Before this, six dialogs listened for Escape on `window` with no
 * preventDefault and no focus containment, ModalShell had no Escape at all,
 * SettingsPanel had no dialog role and no Escape, and the guided tour needed
 * two guards to tell whose key it was. Now every `aria-modal` dialog goes
 * through `useDialogFocus`, which enters the shared dialog stack so only the
 * topmost dialog acts. A component that grows its own keydown listener again
 * fails here.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const componentsDir = join(here, "..", "src", "components");
const read = (...parts: string[]) =>
  readFileSync(join(here, "..", "src", ...parts), "utf8");

const components = readdirSync(componentsDir)
  .filter((name) => name.endsWith(".tsx"))
  .map((name) => [name, readFileSync(join(componentsDir, name), "utf8")] as const);

// The JSX attribute, not the tour overlay's selector string that names it.
const MODAL_ATTRIBUTE = /^\s*aria-modal="true"/m;

test("every aria-modal dialog takes its keyboard from useDialogFocus", () => {
  const modal = components.filter(([, source]) => MODAL_ATTRIBUTE.test(source));
  assert.ok(modal.length >= 12, `expected the app's dialogs, found ${modal.length}`);
  for (const [name, source] of modal) {
    assert.match(source, /useDialogFocus\(/, `${name} is a dialog with no useDialogFocus`);
  }
  // The ones this batch migrated, by name — so a regression names the file.
  for (const name of [
    "ConfirmDialog.tsx",
    "CloseDialog.tsx",
    "HelpModal.tsx",
    "ResearchReportModal.tsx",
    "ModalShell.tsx",
    "SettingsPanel.tsx",
    "QCDrawer.tsx",
  ]) {
    assert.match(read("components", name), /useDialogFocus\(/, name);
  }
});

test("no dialog hand-rolls a keydown listener any more", () => {
  // The guided tour's own window listener is the one legitimate global
  // keydown handler in components/: it ENDS the tour, it is not a dialog.
  for (const [name, source] of components) {
    if (name === "OnboardingOverlay.tsx") continue;
    assert.doesNotMatch(
      source,
      /addEventListener\("keydown"/,
      `${name} registers its own keydown listener instead of using useDialogFocus`,
    );
  }
  // NewSessionDialog's two-level Escape (back to the list, then close) now
  // rides ModalShell's hook through the onEscape prop rather than a listener.
  const dialog = read("components", "NewSessionDialog.tsx");
  assert.match(dialog, /onEscape=\{/);
  const shell = read("components", "ModalShell.tsx");
  assert.match(shell, /onEscape\?\?\s*onClose|onEscape \?\? onClose/);
});

test("Settings is a dialog: role, aria-modal, and a label", () => {
  const settings = read("components", "SettingsPanel.tsx");
  assert.match(settings, /role="dialog"/);
  assert.match(settings, MODAL_ATTRIBUTE);
  assert.match(settings, /aria-label="Settings"/);
});

test("the hook acts only from the top of the shared dialog stack", () => {
  const hook = read("lib", "dialogFocus.ts");
  assert.match(hook, /from "\.\/dialogStack\.ts"/);
  assert.match(hook, /dialogStack\.enter\(token\)/);
  assert.match(hook, /dialogStack\.isTop\(token\)/);
  // A key another dialog already took is not ours either.
  assert.match(hook, /event\.defaultPrevented/);
  // preventDefault BEFORE closing: the "already handled" signal the tour reads.
  assert.match(hook, /event\.preventDefault\(\);\s*\n\s*latestClose\.current\(\)/);
  // Enter and leave live in the same effect as the listener.
  const enter = hook.indexOf("dialogStack.enter(token)");
  const listen = hook.indexOf('document.addEventListener("keydown"');
  const leave = hook.indexOf("leave();");
  const unlisten = hook.indexOf('document.removeEventListener("keydown"');
  assert.ok(enter >= 0 && listen > enter, "enter the stack before listening");
  assert.ok(leave > listen && unlisten > leave, "leave the stack in the cleanup");
});

test("the tour overlay's guards are untouched by the migration", () => {
  // Both guards stay: `defaultPrevented` for the hook family, and the DOM
  // query for any future bare listener — the tour never assumes the stack.
  const overlay = read("components", "OnboardingOverlay.tsx");
  assert.match(overlay, /event\.defaultPrevented/);
  assert.match(overlay, /anotherDialogOwnsEscape\(\)\) return;/);
  assert.match(overlay, /\[role="dialog"\]\[aria-modal="true"\]/);
});
