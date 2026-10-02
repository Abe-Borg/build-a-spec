import { useEffect, useState } from "react";
import { getQcTransportPreference, saveQcTransportPreference, type QcTransportPreference } from "./api";

/** Each visible control reloads disk state; successful saves sync its sibling. */
export function useQcTransportPreference(active: boolean) {
  const [preference, setPreference] = useState<QcTransportPreference | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!active) return;
    let disposed = false;
    setPreference(null);
    setError("");
    getQcTransportPreference().then((value) => {
      if (!disposed) setPreference(value);
    }).catch((e: unknown) => {
      if (!disposed) setError(e instanceof Error ? e.message : String(e));
    });
    const sync = (event: Event) => setPreference((event as CustomEvent<QcTransportPreference>).detail);
    window.addEventListener("qc-transport-saved", sync);
    return () => { disposed = true; window.removeEventListener("qc-transport-saved", sync); };
  }, [active]);
  const change = async (batch: boolean) => {
    if (!preference || preference.locked || pending) return;
    setPending(true);
    setError("");
    try {
      const saved = await saveQcTransportPreference(batch);
      setPreference(saved);
      window.dispatchEvent(new CustomEvent("qc-transport-saved", { detail: saved }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  };
  return { preference, pending, error, onChange: (batch: boolean) => { void change(batch); } };
}
