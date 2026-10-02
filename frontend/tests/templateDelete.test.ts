/**
 * The template studio's delete confirmation belongs to the template whose
 * Delete… was pressed, and to no other.
 *
 * The reported bug: the confirmation was a bare boolean that a deletion never
 * cleared and an import never reset, so deleting a template and then importing
 * its exported .bastemplate opened the new template's Manage view already
 * showing Confirm delete and Keep — one click from deleting it.
 *
 * NewSessionDialog has no DOM harness, so its wiring is pinned at the source
 * level (the tour.test.ts idiom) beside unit tests of the ownership rule it
 * uses. The backend half of the sequence (a re-import is a new template with a
 * fresh id) is pinned by tests/test_templates.py.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  confirmsDeleteOf,
  type PendingTemplateDelete,
} from "../src/lib/templateDelete.ts";

const dialog = readFileSync(
  new URL("../src/components/NewSessionDialog.tsx", import.meta.url),
  "utf8",
);

/** The text from `start` up to (not including) `end`, both required. */
function slice(source: string, start: string, end: string): string {
  const from = source.indexOf(start);
  assert.notEqual(from, -1, `missing ${start}`);
  const to = source.indexOf(end, from + start.length);
  assert.notEqual(to, -1, `missing ${end} after ${start}`);
  return source.slice(from, to);
}

test("a confirmation confirms only the template it was raised for", () => {
  const raised: PendingTemplateDelete = "personal:aaa";
  assert.equal(confirmsDeleteOf(raised, "personal:aaa"), true);
  assert.equal(confirmsDeleteOf(raised, "personal:bbb"), false);
  assert.equal(confirmsDeleteOf(raised, null), false);
  assert.equal(confirmsDeleteOf(raised, undefined), false);
  assert.equal(confirmsDeleteOf(raised, ""), false);
});

test("no confirmation is pending until a Delete… raises one", () => {
  for (const id of ["personal:aaa", "", null, undefined]) {
    assert.equal(confirmsDeleteOf(null, id), false);
  }
  // An empty id is not a template, even if both sides are empty.
  assert.equal(confirmsDeleteOf("", ""), false);
});

test("delete then import: the imported template opens without a confirmation", () => {
  // Replays the Manage view's state through the reported sequence with the
  // rule the dialog renders from. Import mints a fresh personal id.
  const original = "personal:aaa";
  const reimported = "personal:bbb";
  let pending: PendingTemplateDelete = null;

  pending = original; // Delete… on the original
  assert.equal(confirmsDeleteOf(pending, original), true);
  pending = null; // Confirm delete: the confirmation is spent with its template
  assert.equal(confirmsDeleteOf(pending, reimported), false);

  // Even a confirmation left behind (the old bug's state) cannot reach the
  // re-imported copy: ownership is by id, and the id is new.
  assert.equal(confirmsDeleteOf(original, reimported), false);

  // Deleting the re-imported copy takes its own fresh Delete….
  pending = reimported;
  assert.equal(confirmsDeleteOf(pending, reimported), true);
  pending = null; // Keep
  assert.equal(confirmsDeleteOf(pending, reimported), false);
});

test("the dialog holds the confirmation as a template id, never a flag", () => {
  assert.doesNotMatch(dialog, /confirmDelete\b/);
  assert.match(
    dialog,
    /useState<PendingTemplateDelete>\(null\)/,
    "the confirmation state must start empty and hold an id",
  );
  assert.match(dialog, /from "\.\.\/lib\/templateDelete"/);
  // Confirm delete / Keep render only for the selected template's own id.
  assert.match(dialog, /\{!confirmsDeleteOf\(pendingDeleteId, selected\.id\) \?/);
  // Only Delete… raises a confirmation, and only for the template on screen;
  // every other write clears it.
  const writes = [...dialog.matchAll(/setPendingDeleteId\(([^)]*)\)/g)].map(
    (m) => m[1],
  );
  assert.deepEqual(
    writes.filter((arg) => arg !== "null"),
    ["selected.id"],
    "a confirmation may be raised only by Delete… for the selected template",
  );
  assert.match(
    dialog,
    /onClick=\{\(\) => setPendingDeleteId\(selected\.id\)\}[\s\S]{0,80}Delete…/,
  );
});

test("every way into Manage opens a template with no confirmation pending", () => {
  const openManage = slice(dialog, "const openManage = (", "};");
  assert.match(openManage, /setPendingDeleteId\(null\)/);
  assert.match(openManage, /setSelected\(template\)/);
  // Its own details too: an import used to open with the last template's
  // name and description, which Save details would then write onto it.
  assert.match(openManage, /setName\(template\.name\)/);
  assert.match(openManage, /setDescription\(template\.description\)/);
  assert.match(openManage, /setView\("manage"\)/);

  // openManage is the only way the view becomes Manage.
  assert.equal(dialog.match(/setView\("manage"\)/g)?.length, 1);

  // Import (the reported path), a newly saved template, and a template chosen
  // from the list all go through it.
  const importFile = slice(dialog, "const importFile = (", "const chooseImportFile");
  assert.match(importFile, /importTemplate\(file\)[\s\S]*openManage\(created\)/);
  assert.match(dialog, /commitTemplate\(preview\.preview_token\)[\s\S]{0,80}openManage\(created\)/);
  assert.match(dialog, /onClick=\{\(\) => openManage\(template\)\}/);
});

test("Confirm delete deletes only the confirmed template, then clears", () => {
  const handler = slice(dialog, "const target = selected.id;", "Confirm delete");
  const guard = handler.indexOf("if (!confirmsDeleteOf(pendingDeleteId, target)) return;");
  const remove = handler.indexOf("await deleteTemplate(target);");
  const spent = handler.indexOf("setPendingDeleteId(null);", remove);
  assert.notEqual(guard, -1, "the delete must be guarded by the confirmation's owner");
  assert.ok(remove > guard, "the guard must run before the delete request");
  assert.ok(spent > remove, "a completed deletion must clear its confirmation");
  assert.doesNotMatch(handler, /deleteTemplate\(selected\.id\)/);
});

test("Keep cancels the confirmation without deleting", () => {
  const keep = dialog.match(
    /<button type="button" onClick=\{([^}]*)\} className=\{quietBtn\}>Keep<\/button>/,
  );
  assert.ok(keep, "the Keep button must exist");
  assert.equal(keep[1], "() => setPendingDeleteId(null)");
});

test("leaving Manage, Escape and reopening the studio all drop a pending confirmation", () => {
  assert.match(
    dialog,
    /onClick=\{\(\) => \{\s*setPendingDeleteId\(null\);\s*setView\("browse"\);\s*\}\}[\s\S]{0,120}‹ All templates/,
  );
  const escape = slice(dialog, "onEscape={() => {", "} else onCancel();");
  assert.match(escape, /setPendingDeleteId\(null\)/);
  const reopen = slice(dialog, "if (!open) {", "void refresh();");
  assert.match(reopen, /setPendingDeleteId\(null\)/);
});
