import { useCallback, useEffect, useRef, useState } from "react";
import { getAppearance, saveAppearance } from "./api";
import { applyTheme, currentTheme, resolveTheme } from "./theme";

/**
 * The window's colour scheme (lib/theme.ts), owned by App: a scheme is the
 * install's, not the session's, so New session and Open project leave it be.
 *
 * The page is already drawn in the scheme main.py opened it with (`?theme=`,
 * applied by index.html before the first paint). Once mounted, the saved
 * scheme is read once and applied if the page disagrees — a dev page or a
 * hand-opened address carries no `?theme=`.
 *
 * `pick` recolours at once and keeps the choice on disk. A scheme the server
 * did not keep is taken back to the last one it did keep, with its reason in
 * `error` — never to an earlier pick that may be unsaved itself. Saves go out
 * one at a time in click order, so the file ends up holding the last choice,
 * and only the latest pick's answer is acted on.
 */
export function useColorScheme() {
  const [theme, setTheme] = useState<string>(() => currentTheme());
  const [error, setError] = useState("");
  const turn = useRef(0);
  const saves = useRef<Promise<unknown>>(Promise.resolve());
  // The scheme the file holds, as far as this page knows: main.py opened the
  // page in it, and the mount read and every kept save confirm it.
  const persisted = useRef<string>(currentTheme());

  useEffect(() => {
    let cancelled = false;
    const startTurn = turn.current;
    getAppearance()
      .then((saved) => {
        // A pick made since this read started is newer than the file was.
        if (cancelled || turn.current !== startTurn) return;
        const id = resolveTheme(saved.theme);
        persisted.current = id;
        if (id !== currentTheme()) applyTheme(id);
        setTheme(id);
      })
      .catch((failure: unknown) => {
        // The page keeps the scheme it opened in. console.debug, not error:
        // clientLog ships errors to diagnostics as faults, and this is not one.
        console.debug("The saved color scheme could not be read", failure);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const pick = useCallback((id: string) => {
    const next = resolveTheme(id);
    if (next === currentTheme()) return;
    const mine = ++turn.current;
    applyTheme(next);
    setTheme(next);
    setError("");
    const save = saves.current.then(() => saveAppearance(next));
    saves.current = save.catch(() => undefined);
    save.then(
      (kept) => {
        persisted.current = resolveTheme(kept.theme);
      },
      (failure: unknown) => {
        if (mine !== turn.current) return;
        const back = persisted.current;
        applyTheme(back);
        setTheme(back);
        setError(failure instanceof Error ? failure.message : String(failure));
      },
    );
  }, []);

  return { theme, pick, error };
}
