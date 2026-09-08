import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { streamQc } from "../src/lib/api.ts";
import type { QcEvent } from "../src/types.ts";
import { stubSseFetch } from "./sseStub.ts";

const frame = (seq: number, type: string) => ({
  seq,
  ts: "10:00:00",
  type,
  run_id: "run-1",
});

test("the Final QC stream hands its abort signal to fetch", async (t) => {
  const { captured } = stubSseFetch(t, [frame(0, "qc_started")]);
  const controller = new AbortController();

  for await (const event of streamQc(controller.signal)) {
    assert.equal(event.type, "qc_started");
    break;
  }

  assert.equal(captured.signal, controller.signal);
});

test("aborting the signal ends the Final QC stream", async (t) => {
  // The workspace-epoch case: a session/tutorial/project transition aborts
  // the in-flight follower rather than leaving it following the OLD
  // workspace's run — which it would, because the follower reconnects on its
  // own. Without the signal reaching fetch this never returns: the body has
  // no natural end.
  stubSseFetch(t, [frame(0, "qc_started")]);
  const controller = new AbortController();

  const seen: QcEvent[] = [];
  try {
    for await (const event of streamQc(controller.signal)) {
      seen.push(event);
      controller.abort();
    }
  } catch {
    // An aborted body rejects its pending read; the follower's own
    // try/catch reads that as a transport close and probes for status.
  }

  assert.deepEqual(seen.map((event) => event.type), ["qc_started"]);
});

test("breaking out of the Final QC stream releases the body", async (t) => {
  // `readSse`'s `finally` cancels the reader on a plain `break` (the research
  // fix), and it must keep doing so for the QC consumer too: a reconnect or
  // an epoch-change `break` must never leave the browser holding a body no
  // one will read again.
  const { cancelled } = stubSseFetch(t, [
    frame(0, "qc_started"),
    frame(1, "lens_complete"),
  ]);

  for await (const event of streamQc()) {
    if (event.seq === 0) break;
  }

  await cancelled;
});

test("a workspace transition aborts both followers", () => {
  // App.tsx has no DOM harness, so the wiring — the reason this batch exists
  // — is pinned at the source level, the tour/sessionBundle idiom: the one
  // helper every session, project and tutorial transition calls must abort
  // BOTH in-flight streams, and the QC follower must actually hand its
  // controller to the stream and read the abort back.
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

  const epochHelper = app.slice(
    app.indexOf("const advanceWorkspaceEpoch = useCallback("),
  );
  const epochBody = epochHelper.slice(0, epochHelper.indexOf("}, ["));
  assert.match(epochBody, /researchStreamRef\.current\?\.abort\(\)/);
  assert.match(epochBody, /qcStreamRef\.current\?\.abort\(\)/);

  const follower = app.slice(app.indexOf("const followQc = useCallback("));
  const followerBody = follower.slice(0, follower.indexOf("}, ["));
  assert.match(followerBody, /qcStreamRef\.current = controller/);
  assert.match(followerBody, /streamQc\(controller\.signal\)/);
  assert.match(followerBody, /controller\.signal\.aborted/);
  assert.match(
    followerBody,
    /if \(qcStreamRef\.current === controller\) \{\s*qcStreamRef\.current = null;/,
  );
});
