import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/lib/tour.ts", import.meta.url), "utf8");
const overlaySource = readFileSync(
  new URL("../src/components/OnboardingOverlay.tsx", import.meta.url),
  "utf8",
);
const newSessionSource = readFileSync(
  new URL("../src/components/NewSessionDialog.tsx", import.meta.url),
  "utf8",
);

test("guided tour is passive data with stable anchors", () => {
  assert.doesNotMatch(source, /\bactions?\s*:/);
  assert.doesNotMatch(source, /\bresolve\s*:/);
  assert.doesNotMatch(
    source,
    /DEMO_PROFILE|DISCIPLINES|TUTORIAL_SUGGESTIONS|syntheticSuggestions/,
  );
  assert.match(source, /anchor:\s*"chat-pane"/);
  assert.match(source, /anchorSelector/);
});

test("tour covers the dynamic heading and new-session choices", () => {
  assert.match(source, /id:\s*"project-heading"[\s\S]*?anchor:\s*"project-heading"/);
  assert.match(source, /id:\s*"finish"[\s\S]*?anchor:\s*"new-session"/);
  assert.match(source, /blank slate/i);
  assert.match(source, /templates/i);
});

test("new-session dialog exposes only blank slate as an active path", () => {
  assert.match(newSessionSource, /onClick=\{onBlankSlate\}[\s\S]*?Start with a blank slate/);
  assert.match(newSessionSource, /disabled[\s\S]*?Start from a template/);
  assert.match(newSessionSource, /disabled[\s\S]*?Load your own template/);
  assert.match(newSessionSource, /features are in development/i);
  assert.match(newSessionSource, /event\.key === "Escape"/);
});

test("tour overlay blocks the app and offers End tour throughout", () => {
  assert.match(overlaySource, /fixed inset-0 z-\[60\] cursor-default/);
  assert.doesNotMatch(overlaySource, /className="pointer-events-none fixed inset-0/);
  assert.ok((overlaySource.match(/End tour/g) ?? []).length >= 3);
  assert.doesNotMatch(overlaySource, /runResearch|runQc|prefill|confirm-first/);
});
