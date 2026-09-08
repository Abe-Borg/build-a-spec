/**
 * Busy/error state for a group of fetch-then-save downloads.
 *
 * A bare `<a download>` has no failure mode a user can see: a 409, a 500,
 * or a backend that is simply gone downloads nothing and says nothing —
 * in the native shell the click just looks dead. Routing each click
 * through a fetch (`downloadAttachment` in lib/api.ts) gives the control a
 * preparing state while the bytes stream and the server's exact message
 * when they do not.
 *
 * `busy` is the KEY of the download in flight, not a boolean: a surface
 * with several downloads (the Export menu has seven) swaps the label on
 * the one that was clicked while `busy !== null` locks all of them.
 * `error` is one flat string for the group — the surfaces render it in a
 * single strip or alert beside the controls.
 *
 * Generalized from the Final QC report downloads, which keep their own
 * wrapper (`useQcReportDownloads`) over this hook.
 */
import { useCallback, useState } from "react";

export interface Downloads<K extends string> {
  busy: K | null;
  error: string;
  clearError: () => void;
  /** Run `fetchAndSave` under `key`; a rejection becomes `error`. */
  download: (key: K, fetchAndSave: () => Promise<void>) => Promise<void>;
}

export function useDownloads<K extends string = string>(): Downloads<K> {
  const [busy, setBusy] = useState<K | null>(null);
  const [error, setError] = useState("");
  const clearError = useCallback(() => setError(""), []);
  const download = useCallback(
    async (key: K, fetchAndSave: () => Promise<void>) => {
      setBusy(key);
      setError("");
      try {
        await fetchAndSave();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(null);
      }
    },
    [],
  );
  return { busy, error, clearError, download };
}
