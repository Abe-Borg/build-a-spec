import { useState } from "react";
import { saveApiKey } from "../lib/api";

interface Props {
  onSaved: () => void;
}

export default function ApiKeyBanner({ onSaved }: Props) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!value.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await saveApiKey(value.trim());
      setValue("");
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-b border-edge bg-raised px-5 py-3" data-tour="key-banner">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm text-ink-dim">
          Enter your Anthropic API key to start building:
        </span>
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="sk-ant-…"
          className="min-w-64 flex-1 rounded-lg border border-edge bg-bg px-3 py-1.5 text-sm text-ink outline-none placeholder:text-ink-faint focus:border-accent"
        />
        <button
          onClick={submit}
          disabled={busy || !value.trim()}
          className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-40"
        >
          {busy ? "Saving…" : "Save key"}
        </button>
      </div>
      {error && <p className="mt-2 text-xs text-err">{error}</p>}
      <p className="mt-1.5 text-xs text-ink-faint">
        Stored in your OS credential manager when available, otherwise in your
        user config folder. Never sent anywhere except the Anthropic API.
      </p>
    </div>
  );
}
