/**
 * The one fetch-then-save (`downloadAttachment`) and the Export menu that
 * now goes through it.
 *
 * The specification exports were bare `<a download>` links — no failure
 * mode a user can see, which `useQcReportDownloads` had already fixed for
 * the QC reports only. The failure-path tests follow `qcApi.test.ts`
 * (swap `globalThis.fetch`, assert on a non-OK response). The success path
 * needs a DOM, which node does not have: the shim below is the first in
 * the suite — `URL.createObjectURL`, `document.createElement` and
 * `document.body.appendChild` stubbed just far enough to observe the
 * anchor the save path builds.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  downloadAttachment,
  downloadProjectFile,
  exportDocxUrl,
  filenameFromDisposition,
} from "../src/lib/api.ts";

test("the attachment filename comes from Content-Disposition, else the fallback", () => {
  assert.equal(
    filenameFromDisposition('attachment; filename="21 13 13.docx"', "x.docx"),
    "21 13 13.docx",
  );
  assert.equal(
    filenameFromDisposition("attachment; filename=plain.docx", "x.docx"),
    "plain.docx",
  );
  // A trailing RFC 5987 parameter must not ride into the name — the
  // project-file copy of this regex stopped only at `"`, and did.
  assert.equal(
    filenameFromDisposition(
      "attachment; filename=\"spec.docx\"; filename*=UTF-8''spec.docx",
      "x.docx",
    ),
    "spec.docx",
  );
  assert.equal(
    filenameFromDisposition("attachment; filename=spec.docx; size=12", "x.docx"),
    "spec.docx",
  );
  assert.equal(filenameFromDisposition(null, "fallback.docx"), "fallback.docx");
  assert.equal(filenameFromDisposition("inline", "fallback.docx"), "fallback.docx");
  assert.equal(filenameFromDisposition('filename=""', "fallback.docx"), "fallback.docx");
});

test("a refused download surfaces the server's own message", async (t) => {
  const originalFetch = globalThis.fetch;
  let capturedInput: RequestInfo | URL | undefined;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async (input) => {
    capturedInput = input;
    return new Response(
      JSON.stringify({ ok: false, error: "A model turn is streaming." }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    );
  };

  await assert.rejects(
    downloadAttachment("/api/export/docx?mode=preserved", "specification.docx", "Export"),
    /A model turn is streaming\./,
  );
  assert.equal(capturedInput, "/api/export/docx?mode=preserved");
});

test("a non-JSON failure body still produces a readable, labelled error", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () =>
    new Response("<html>proxy error</html>", { status: 502 });

  await assert.rejects(
    downloadAttachment("/api/import/original", "original-upload.docx", "Export"),
    /^Error: Export failed \(502\)$/,
  );
  // The default label, for a caller that names none.
  await assert.rejects(
    downloadAttachment("/api/import/original", "original-upload.docx"),
    /^Error: download failed \(502\)$/,
  );
});

test("the export URL builder covers every shape the menu offers", () => {
  assert.equal(exportDocxUrl({ mode: "preserved" }), "/api/export/docx?mode=preserved");
  assert.equal(exportDocxUrl({ mode: "source" }), "/api/export/docx?mode=source");
  assert.equal(
    exportDocxUrl({ mode: "normalized" }),
    "/api/export/docx?mode=normalized",
  );
  assert.equal(exportDocxUrl({ redline: "master" }), "/api/export/docx?redline=master");
  assert.equal(
    exportDocxUrl({ redline: "version", base: 3 }),
    "/api/export/docx?redline=version&base=3",
  );
});

test("the project file download surfaces a refusal's message and scopes the tutorial", async (t) => {
  // The knowing behavior change of folding downloadProjectFile into the
  // shared helper: a refused save used to be a bare `save failed (N)`.
  const originalFetch = globalThis.fetch;
  const inputs: (RequestInfo | URL)[] = [];
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async (input) => {
    inputs.push(input);
    return new Response(
      JSON.stringify({ ok: false, error: "The tutorial workspace has no project to save." }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    );
  };

  await assert.rejects(downloadProjectFile("tutorial"), /tutorial workspace has no project/);
  assert.equal(inputs[0], "/api/project/save?scope=tutorial");

  globalThis.fetch = async (input) => {
    inputs.push(input);
    return new Response("nope", { status: 500 });
  };
  await assert.rejects(downloadProjectFile(), /^Error: save failed \(500\)$/);
  assert.equal(inputs[1], "/api/project/save");
});

test("a successful download saves the bytes under the server's filename", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const originalFetch = globalThis.fetch;
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const hadDocument = Object.prototype.hasOwnProperty.call(globalThis, "document");
  const originalDocument = (globalThis as { document?: unknown }).document;
  t.after(() => {
    globalThis.fetch = originalFetch;
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    if (hadDocument) {
      (globalThis as { document?: unknown }).document = originalDocument;
    } else {
      delete (globalThis as { document?: unknown }).document;
    }
  });

  globalThis.fetch = async () =>
    new Response(new Blob(["PK"]), {
      status: 200,
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": 'attachment; filename="SECTION 21 13 13 - REDLINE.docx"',
      },
    });

  const revoked: string[] = [];
  URL.createObjectURL = () => "blob:test/1";
  URL.revokeObjectURL = (url: string) => {
    revoked.push(url);
  };
  const anchor = { href: "", download: "", clicks: 0, removed: 0 } as {
    href: string;
    download: string;
    clicks: number;
    removed: number;
    click?: () => void;
    remove?: () => void;
  };
  anchor.click = () => {
    anchor.clicks += 1;
  };
  anchor.remove = () => {
    anchor.removed += 1;
  };
  const appended: unknown[] = [];
  (globalThis as { document?: unknown }).document = {
    createElement: () => anchor,
    body: { appendChild: (node: unknown) => appended.push(node) },
  };

  await downloadAttachment(
    "/api/export/docx?redline=master",
    "specification - REDLINE.docx",
    "Export",
  );

  assert.equal(anchor.download, "SECTION 21 13 13 - REDLINE.docx");
  assert.equal(anchor.href, "blob:test/1");
  assert.equal(anchor.clicks, 1);
  assert.equal(anchor.removed, 1);
  assert.deepEqual(appended, [anchor]);
  // Revocation is deferred: revoking synchronously after click() can cancel
  // the download in some browsers.
  assert.deepEqual(revoked, []);
  t.mock.timers.tick(1000);
  assert.deepEqual(revoked, ["blob:test/1"]);
});

test("the Export menu has no bare download anchors left", () => {
  // A text-level pin (the sessionBundle.test.ts idiom): the panel has no
  // DOM harness, and nothing else would notice an `<a download>` creeping
  // back in — it would simply be a control that fails silently again.
  const here = dirname(fileURLToPath(import.meta.url));
  const panel = readFileSync(
    join(here, "..", "src", "components", "ArtifactPanel.tsx"),
    "utf8",
  );
  // An element carries an href; the comments that name the defect do not.
  assert.doesNotMatch(
    panel,
    /<a\s[^>]*\bhref=[^>]*\sdownload\b|<a\s[^>]*\sdownload\b[^>]*\bhref=/,
    "a bare <a download> is back",
  );
  // The one export control that declares a capability of its own must keep
  // it through the anchor → button conversion (tour.test.ts scrapes it).
  assert.match(panel, /data-capability="import\.source-output"/);
  // Every export goes through the shared helper, never a hand-built fetch.
  assert.match(panel, /downloadAttachment\(url, fallbackName, "Export"\)/);
});
