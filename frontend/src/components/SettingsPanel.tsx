import { useCallback, useEffect, useState } from "react";
import type { Health, KeyStatus, UsageSummary } from "../types";
import {
  checkUpdate,
  deleteKey,
  getKeyStatus,
  saveApiKey,
  testKey,
} from "../lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
  health: Health | null;
  /** Called after the stored key changes so the app can refresh health. */
  onKeyChange: () => void;
  /** Session usage snapshot (WI4 meter); null until first load. */
  usage?: UsageSummary | null;
}

const CATEGORY_LABEL: Record<string, string> = {
  interview: "Interview",
  research: "Research",
  audit: "Audit",
  qc: "Final QC",
};

/** Compact token formatting: 12345 → "12.3k". */
function fmt(n: number | undefined): string {
  if (!n) return "0";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function UsageTable({ usage }: { usage: UsageSummary }) {
  const cats = Object.keys(usage.categories);
  if (cats.length === 0) {
    return (
      <p className="mt-2 text-xs text-ink-faint">
        No spend recorded yet this session.
      </p>
    );
  }
  const cell = "px-2 py-1 text-right tabular-nums";
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-[11px] text-ink-dim">
        <thead className="text-ink-faint">
          <tr className="border-b border-edge">
            <th className="px-2 py-1 text-left font-medium">Category</th>
            <th className={cell + " font-medium"}>In</th>
            <th className={cell + " font-medium"}>Out</th>
            <th className={cell + " font-medium"}>Cache r/w</th>
            <th className={cell + " font-medium"}>Web</th>
            <th className={cell + " font-medium"}>Est.</th>
          </tr>
        </thead>
        <tbody>
          {cats.map((cat) => {
            const b = usage.categories[cat];
            const est = usage.estimated_cost_usd.by_category[cat] ?? 0;
            return (
              <tr key={cat} className="border-b border-edge/50">
                <td className="px-2 py-1 text-left text-ink">
                  {CATEGORY_LABEL[cat] ?? cat}
                </td>
                <td className={cell}>{fmt(b.input_tokens)}</td>
                <td className={cell}>{fmt(b.output_tokens)}</td>
                <td className={cell}>
                  {fmt(b.cache_read_input_tokens)}/
                  {fmt(b.cache_creation_input_tokens)}
                </td>
                <td className={cell}>{b.web_search_requests ?? 0}</td>
                <td className={cell + " text-ink"}>${est.toFixed(3)}</td>
              </tr>
            );
          })}
          <tr className="font-medium text-ink">
            <td className="px-2 py-1 text-left">
              Total ({usage.turns} turn{usage.turns === 1 ? "" : "s"})
            </td>
            <td className={cell}>{fmt(usage.totals.input_tokens)}</td>
            <td className={cell}>{fmt(usage.totals.output_tokens)}</td>
            <td className={cell}>
              {fmt(usage.totals.cache_read_input_tokens)}/
              {fmt(usage.totals.cache_creation_input_tokens)}
            </td>
            <td className={cell}>{usage.totals.web_search_requests ?? 0}</td>
            <td className={cell}>
              ${usage.estimated_cost_usd.total.toFixed(3)}
            </td>
          </tr>
        </tbody>
      </table>
      {usage.cache_saved_usd > 0 && (
        <p className="mt-2 text-[11px] text-ok">
          Prompt caching saved ≈ ${usage.cache_saved_usd.toFixed(3)} this
          session.
        </p>
      )}
      <p className="mt-1 text-[11px] text-ink-faint">
        Estimates from Anthropic list pricing — actual billing may differ.
      </p>
    </div>
  );
}

const SOURCE_LABEL: Record<KeyStatus["source"], string> = {
  env: "Environment variable (read-only)",
  keyring: "OS credential manager",
  file: "Key file (config folder)",
  none: "Not configured",
};

type TestResult = { ok: boolean; error?: string } | null;

export default function SettingsPanel({
  open,
  onClose,
  health,
  onKeyChange,
  usage,
}: Props) {
  const [status, setStatus] = useState<KeyStatus | null>(null);
  const [replaceValue, setReplaceValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<TestResult>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [updateMsg, setUpdateMsg] = useState<string | null>(null);

  const refreshStatus = useCallback(() => {
    getKeyStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    if (open) {
      refreshStatus();
      setReplaceValue("");
      setTestResult(null);
      setConfirmRemove(false);
      setUpdateMsg(null);
    }
  }, [open, refreshStatus]);

  if (!open) return null;

  const envLocked = status?.env_locked === true;

  const test = async () => {
    setBusy(true);
    setTestResult(null);
    const result = await testKey(replaceValue.trim() || undefined);
    setTestResult(result);
    setBusy(false);
  };

  const saveAfterTest = async () => {
    const key = replaceValue.trim();
    if (!key) return;
    setBusy(true);
    setTestResult(null);
    // Test first — save only if the key authenticates.
    const result = await testKey(key);
    if (!result.ok) {
      setTestResult(result);
      setBusy(false);
      return;
    }
    try {
      await saveApiKey(key);
      setReplaceValue("");
      setTestResult({ ok: true });
      refreshStatus();
      onKeyChange();
    } catch (e) {
      setTestResult({ ok: false, error: e instanceof Error ? e.message : String(e) });
    }
    setBusy(false);
  };

  const remove = async () => {
    setBusy(true);
    try {
      const next = await deleteKey();
      setStatus(next);
      setConfirmRemove(false);
      onKeyChange();
    } catch {
      // Leave the current status; the banner/health will reflect reality.
    }
    setBusy(false);
  };

  const runUpdateCheck = async () => {
    setUpdateMsg("Checking…");
    try {
      const r = await checkUpdate(true);
      if (r.status === "UPDATE_AVAILABLE" && r.version) {
        setUpdateMsg(`v${r.version} available — see the header to install.`);
      } else if (r.error) {
        setUpdateMsg(`Check failed: ${r.error}`);
      } else {
        setUpdateMsg("You're on the latest version.");
      }
    } catch {
      setUpdateMsg("Update check failed.");
    }
  };

  const label = "text-[11px] font-medium tracking-wide text-ink-dim uppercase";
  const btn =
    "rounded-lg border border-edge bg-raised px-3 py-1.5 text-sm text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40";

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-6 pt-16"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-edge px-5 py-3">
          <h2 className="font-[family-name:var(--font-display)] text-lg font-semibold">
            Settings
          </h2>
          <button
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-ink-dim transition-colors hover:text-ink"
            title="Close"
          >
            ✕
          </button>
        </div>

        <div className="max-h-[70vh] space-y-6 overflow-y-auto px-5 py-5">
          {/* --- API key --- */}
          <section>
            <p className={label}>Anthropic API key</p>
            <div className="mt-2 flex items-center gap-2 text-sm">
              <span
                className={`h-2 w-2 rounded-full ${
                  status?.present ? "bg-ok" : "bg-warn"
                }`}
              />
              <span className="text-ink">
                {status ? SOURCE_LABEL[status.source] : "…"}
              </span>
              {status?.present && (
                <span className="font-mono text-xs text-ink-faint">
                  {status.masked}
                </span>
              )}
            </div>

            {envLocked ? (
              <p className="mt-2 text-xs text-ink-faint">
                The key comes from the <code>ANTHROPIC_API_KEY</code>{" "}
                environment variable and can only be changed there.
              </p>
            ) : (
              <div className="mt-3 space-y-2">
                <input
                  type="password"
                  value={replaceValue}
                  onChange={(e) => setReplaceValue(e.target.value)}
                  placeholder={status?.present ? "Replace key — sk-ant-…" : "sk-ant-…"}
                  className="w-full rounded-lg border border-edge bg-bg px-3 py-1.5 text-sm text-ink outline-none placeholder:text-ink-faint focus:border-accent"
                />
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    className={btn}
                    onClick={test}
                    disabled={busy}
                    title="Check the key without saving"
                  >
                    Test
                  </button>
                  <button
                    className={btn}
                    onClick={saveAfterTest}
                    disabled={busy || !replaceValue.trim()}
                    title="Test the key, then save it on success"
                  >
                    {busy ? "Working…" : "Save"}
                  </button>
                  {status?.present &&
                    (confirmRemove ? (
                      <span className="flex items-center gap-2 text-xs">
                        <span className="text-err">Remove the stored key?</span>
                        <button className={btn} onClick={remove} disabled={busy}>
                          Remove
                        </button>
                        <button
                          className={btn}
                          onClick={() => setConfirmRemove(false)}
                        >
                          Keep
                        </button>
                      </span>
                    ) : (
                      <button
                        className={`${btn} border-err/40 text-err hover:border-err`}
                        onClick={() => setConfirmRemove(true)}
                        disabled={busy}
                      >
                        Remove
                      </button>
                    ))}
                </div>
                {testResult && (
                  <p
                    className={`text-xs ${
                      testResult.ok ? "text-ok" : "text-err"
                    }`}
                  >
                    {testResult.ok
                      ? "Key authenticated ✓"
                      : `Key rejected: ${testResult.error}`}
                  </p>
                )}
              </div>
            )}
            <p className="mt-2 text-xs text-ink-faint">
              Stored in your OS credential manager when available, otherwise in
              your user config folder. Never sent anywhere except the Anthropic
              API.
            </p>
          </section>

          {/* --- Usage (WI4) --- */}
          <section>
            <p className={label}>Usage this session</p>
            {usage ? (
              <UsageTable usage={usage} />
            ) : (
              <p className="mt-2 text-xs text-ink-faint">
                No spend recorded yet this session.
              </p>
            )}
          </section>

          {/* --- About --- */}
          <section>
            <p className={label}>About</p>
            <div className="mt-2 space-y-1 text-sm text-ink-dim">
              <p>
                Build-a-Spec{" "}
                <span className="text-ink">v{health?.version ?? "…"}</span>
              </p>
              <p>
                Model <span className="text-ink">{health?.model ?? "…"}</span>
              </p>
              <button
                className="text-accent underline underline-offset-2 hover:text-accent-hover"
                onClick={runUpdateCheck}
              >
                Check for updates
              </button>
              {updateMsg && (
                <p className="text-xs text-ink-faint">{updateMsg}</p>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
