/**
 * Frontend error capture for the Developer-tools diagnostics record.
 *
 * Installs window `error` / `unhandledrejection` listeners and wraps
 * `console.error` / `console.warn` (originals always run first), reporting
 * each occurrence to `POST /api/diagnostics/client-event`, where it lands
 * in the local activity log and the trace. The frontend otherwise renders
 * errors only into transient React state — after the fact there is no
 * record at all, which is exactly what this closes.
 *
 * Deliberately NOT routed through `lib/api.ts`: those helpers throw on
 * failure, and a reporter must never throw, recurse (a failed report
 * console.error'ing would re-enter the wrapper), or block the UI. Reports
 * are throttled client-side — one per distinct `kind:message` per 10s and
 * a hard per-session cap — so a render-loop error storm cannot flood the
 * backend; the server bounds payload size and stays dumb.
 */

const THROTTLE_MS = 10_000;
const SESSION_CAP = 40;
const MAX_MESSAGE = 2_000;
const MAX_STACK = 4_000;
const MAX_SOURCE = 300;

type ClientEventKind =
  | "error"
  | "unhandledrejection"
  | "console.error"
  | "console.warn";

let installed = false;
let reporting = false;
let sent = 0;
let capNoticeSent = false;
const lastSent = new Map<string, number>();

function post(kind: ClientEventKind, message: string, stack = "", source = "") {
  if (reporting || sent >= SESSION_CAP + 1) return;
  const key = `${kind}:${message}`;
  const now = Date.now();
  const last = lastSent.get(key);
  if (last !== undefined && now - last < THROTTLE_MS) return;
  lastSent.set(key, now);
  if (sent >= SESSION_CAP) {
    if (capNoticeSent) return;
    capNoticeSent = true;
    message = `client diagnostics cap reached (${SESSION_CAP} reports); further reports suppressed this session`;
    stack = "";
    source = "";
    kind = "error";
  }
  sent += 1;
  reporting = true;
  try {
    void fetch("/api/diagnostics/client-event", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind,
        message: message.slice(0, MAX_MESSAGE),
        stack: stack.slice(0, MAX_STACK),
        source: source.slice(0, MAX_SOURCE),
      }),
      keepalive: true,
    }).catch(() => {});
  } catch {
    // fetch itself threw (no network stack at all) — drop the report.
  } finally {
    reporting = false;
  }
}

function describe(value: unknown): string {
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Install once, before the React root renders. Never throws. */
export function installClientDiagnostics(): void {
  if (installed) return;
  installed = true;
  try {
    window.addEventListener("error", (event) => {
      const err = event.error;
      post(
        "error",
        event.message || describe(err),
        err instanceof Error ? err.stack ?? "" : "",
        event.filename ? `${event.filename}:${event.lineno ?? 0}` : "",
      );
    });
    window.addEventListener("unhandledrejection", (event) => {
      const reason: unknown = event.reason;
      post(
        "unhandledrejection",
        describe(reason),
        reason instanceof Error ? reason.stack ?? "" : "",
      );
    });
    const originalError = console.error.bind(console);
    const originalWarn = console.warn.bind(console);
    console.error = (...args: unknown[]) => {
      originalError(...args);
      post("console.error", args.map(describe).join(" "));
    };
    console.warn = (...args: unknown[]) => {
      originalWarn(...args);
      post("console.warn", args.map(describe).join(" "));
    };
  } catch {
    // A broken collector must never break the app.
  }
}
