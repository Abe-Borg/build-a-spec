/**
 * The "save before you lose this?" gate is reachable from the window that
 * asks for it — pinned at the source level, because App.tsx and the dialogs
 * have no DOM harness (the dialogs.test.ts / nextSection.test.ts idiom).
 *
 * The bug: with unsaved work, Templates → Start and New session → existing
 * project brief → Start with the brief both request the gate from INSIDE the
 * template window. That window is a z-[70] ModalShell and deliberately stays
 * open underneath, so Cancel can return to it as it was. The gate rendered at
 * z-[60] — behind its own caller — so every click on it landed on the
 * template window and the app looked stuck until the window was closed. The
 * dialog stack already routed Escape and Tab to the gate; the mouse could not
 * reach it.
 *
 * What is pinned:
 * 1. The layer rule, over every component: no ordinary layer reaches the
 *    gate's, and only the elevated confirmations sit above it. Read from the
 *    string literals by the TypeScript parser (the themeTokens.test.ts
 *    approach), so a comment naming a layer never counts.
 * 2. Both entry paths open the gate from the template window and leave the
 *    window open beneath it — the situation the layer rule exists for.
 * 3. The answers keep their meaning: Cancel only closes the gate; Discard
 *    and a written Save start the ORIGINALLY requested template (with the
 *    brief and its discipline on the brief path); the window closes only
 *    once the start succeeds.
 * 4. Focus: the gate takes it (Save, the old autoFocus target) through the
 *    shared hook, and Cancel/Escape return it to the Start button.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const srcDir = join(here, "..", "src");
const read = (...parts: string[]) => readFileSync(join(srcDir, ...parts), "utf8");

const app = read("App.tsx");
const gateSource = read("components", "CloseDialog.tsx");
const shellSource = read("components", "ModalShell.tsx");
const confirmSource = read("components", "ConfirmDialog.tsx");
const newSession = read("components", "NewSessionDialog.tsx");

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...tsxFiles(path));
    else if (entry.name.endsWith(".tsx")) out.push(path);
  }
  return out.sort();
}

/** Every string literal's text (a template's `${…}` walked as code). */
function stringLiterals(text: string, fileName: string): string[] {
  const source = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) out.push(node.text);
    else if (ts.isTemplateExpression(node)) {
      out.push([node.head.text, ...node.templateSpans.map((span) => span.literal.text)].join(" "));
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return out;
}

/** The z-index utilities in a class list: `z-50` and `z-[70]` alike. */
function zLayers(classList: string): number[] {
  return [...classList.matchAll(/(?:^|\s)z-(?:\[(\d+)\]|(\d+))(?=\s|$)/g)].map((m) =>
    Number(m[1] ?? m[2]),
  );
}

function layersIn(text: string, fileName: string): number[] {
  return stringLiterals(text, fileName).flatMap(zLayers);
}

/** The single layer of a component whose overlay has exactly one. */
function onlyLayer(text: string, fileName: string): number {
  const layers = layersIn(text, fileName);
  assert.equal(layers.length, 1, `${fileName}: expected one z-layer, found ${JSON.stringify(layers)}`);
  return layers[0];
}

const GATE = onlyLayer(gateSource, "CloseDialog.tsx");
const WINDOW = onlyLayer(shellSource, "ModalShell.tsx");
const ELEVATED = Number(/elevated \? "z-\[(\d+)\]"/.exec(confirmSource)?.[1]);

test("the gate stacks above the template window and below the elevated confirmations", () => {
  assert.ok(Number.isFinite(ELEVATED), "ConfirmDialog's elevated layer must be readable");
  assert.ok(
    GATE > WINDOW,
    `the save gate (z-${GATE}) must sit above the ModalShell windows (z-${WINDOW}) that request it`,
  );
  assert.ok(GATE < ELEVATED, `the save gate (z-${GATE}) stays below the elevated confirmations (z-${ELEVATED})`);
  // The template window really is that shell.
  assert.match(newSession, /import \{ ModalShell[^}]*\} from "\.\/ModalShell"/);
  assert.match(newSession, /<ModalShell\b/);
});

test("no ordinary layer anywhere in the app reaches the gate's", () => {
  // A window at or above the gate could request a save gate and hide it, the
  // bug this file exists for. The only layers allowed there are the elevated
  // confirmations, which outrank everything by design.
  const offenders: string[] = [];
  for (const file of tsxFiles(srcDir)) {
    if (file.endsWith(join("components", "CloseDialog.tsx"))) continue;
    for (const layer of layersIn(readFileSync(file, "utf8"), file)) {
      if (layer >= GATE && layer < ELEVATED) offenders.push(`${relative(srcDir, file)}: z-${layer}`);
      if (layer > ELEVATED) offenders.push(`${relative(srcDir, file)}: z-${layer} (above the elevated layer)`);
    }
  }
  assert.deepEqual(offenders, [], "layers between the save gate and the elevated confirmations");
});

test("the layer reader sees real class lists and ignores comments", () => {
  assert.deepEqual(zLayers("fixed inset-0 z-[70] flex"), [70]);
  assert.deepEqual(zLayers("fixed inset-0 z-50 flex"), [50]);
  assert.deepEqual(zLayers("relative -z-10 hover:z-20 z-[80]x"), []);
  assert.deepEqual(layersIn('// z-[99]\nconst a = "fixed z-[61]";\n/* z-[98] */', "x.tsx"), [61]);
  assert.deepEqual(layersIn("const c = `fixed ${on ? \"z-[80]\" : \"z-[60]\"}`;", "x.tsx"), [80, 60]);
});

function body(pattern: RegExp, what: string): string {
  const found = pattern.exec(app)?.[0];
  assert.ok(found, `${what} must exist`);
  return found;
}

test("templates → Start gates from inside the template window, which stays open", () => {
  // The window hands Start to the gated request, never straight to the start.
  assert.match(
    app,
    /onStartTemplate=\{\(templateId\) => void requestStartTemplate\(templateId\)\}/,
  );
  assert.match(newSession, /brief \? startBrief\(template\.id\) : onStartTemplate\(template\.id\)/);
  const request = body(/const requestStartTemplate = async[\s\S]*?\n  \};/, "requestStartTemplate");
  assert.match(request, /if \(await isUnsaved\(\)\) \{\s*setSaveGate\(\{ kind: "start-template", templateId \}\);/);
  // Nothing closes the window before the user answers: it is beneath the gate.
  assert.doesNotMatch(request, /setNewSessionOpen\(/);
  assert.match(app, /saveGate\?\.kind === "start-template"\s*\?\s*"Start from this template\?"/);
});

test("new session → project brief → Start with the brief gates from the same window", () => {
  assert.match(app, /onStartBrief=\{\(file, opts\) => void requestStartFromBrief\(file, opts\)\}/);
  const startBrief = /const startBrief = \(templateId\?: string\) => \{[\s\S]*?\n  \};/.exec(newSession)?.[0];
  assert.ok(startBrief, "NewSessionDialog's startBrief must exist");
  assert.match(startBrief, /onStartBrief\(brief\.file, \{\s*discipline: briefDiscipline\.trim\(\) \|\| undefined,\s*templateId,\s*\}\)/);
  assert.match(newSession, /\{brief \? "Start with the brief" : "Start"\}/);
  const request = body(/const requestStartFromBrief = async[\s\S]*?\n  \};/, "requestStartFromBrief");
  assert.match(request, /if \(await isUnsaved\(\)\) \{\s*setSaveGate\(\{ kind: "start-brief", file, \.\.\.opts \}\);/);
  assert.doesNotMatch(request, /setNewSessionOpen\(/);
  // The pending gate keeps all three: the file, the discipline, the template.
  assert.match(
    app,
    /kind: "start-brief";\s*file: File;\s*discipline\?: string;\s*templateId\?: string;/,
  );
});

test("the gate's answers start the requested template, or change nothing", () => {
  const runGate = body(/const runGate = \([\s\S]*?\n  \};/, "runGate");
  assert.match(runGate, /gate\.kind === "start-template"\) \{\s*void doInstantiateTemplate\(gate\.templateId\);/);
  // The brief path is the fall-through branch; it carries every choice made.
  assert.match(
    runGate,
    /else \{\s*void doStartFromBrief\(gate\.file, \{\s*discipline: gate\.discipline,\s*templateId: gate\.templateId,\s*\}\);/,
  );
  // Save proceeds only once a file was written; Discard always proceeds.
  const onSave = body(/const onGateSave = async \(\) => \{[\s\S]*?\n  \};/, "onGateSave");
  assert.match(onSave, /const saved = await saveProjectFile\(\);\s*setSaveGate\(null\);[\s\S]*if \(saved\.ok\) runGate\(gate\);/);
  const onDiscard = body(/const onGateDiscard = \(\) => \{[\s\S]*?\n  \};/, "onGateDiscard");
  assert.match(onDiscard, /setSaveGate\(null\);\s*if \(gate\) runGate\(gate\);/);
  // Cancel closes the gate and nothing else: the section and the window stay.
  const gate = body(/<CloseDialog\s+open=\{saveGate !== null\}[\s\S]*?\/>/, "the in-app save gate");
  assert.match(gate, /onCancel=\{\(\) => setSaveGate\(null\)\}/);
  // The window closes only once the requested start has gone through.
  for (const name of ["doInstantiateTemplate", "doStartFromBrief"]) {
    const start = body(new RegExp(`async function ${name}\\([\\s\\S]*?\\n  \\}\\n`), name);
    const applied = start.indexOf("if (!applySessionBundle(session)) return;");
    const closed = start.indexOf("setNewSessionOpen(false);");
    assert.ok(applied >= 0 && closed > applied, `${name} closes the window only after the start applies`);
    assert.doesNotMatch(start.slice(start.indexOf("} catch")), /setNewSessionOpen\(false\)/, `${name} keeps the window on failure`);
  }
});

test("focus moves into the gate and comes back to the Start button", () => {
  // The shared hook: initial focus on Save, Escape cancels, Tab is contained,
  // and the element focused before it opened (the Start button) gets focus
  // back when it closes. The dialog stack makes the gate — mounted last — the
  // only dialog that answers keys while it is up.
  assert.match(gateSource, /useDialogFocus\(open, panelRef, saveRef, onCancel\)/);
  assert.match(gateSource, /ref=\{saveRef\}\s*onClick=\{onSave\}/);
  assert.match(gateSource, /role="dialog"\s*aria-modal="true"/);
  // A click on the backdrop is an answer (Cancel), and one on the panel is not.
  assert.match(gateSource, /onClick=\{onCancel\}\s*role="dialog"/);
  assert.match(gateSource, /onClick=\{\(e\) => e\.stopPropagation\(\)\}/);
  const hook = read("lib", "dialogFocus.ts");
  assert.match(hook, /previouslyFocused\?\.isConnected\) previouslyFocused\.focus\(\)/);
});
