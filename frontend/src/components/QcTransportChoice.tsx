import { useId } from "react";
import type { QcTransportPreference } from "../lib/api";

export default function QcTransportChoice({ preference, pending, error, onChange }: {
  preference: QcTransportPreference | null;
  pending: boolean;
  error: string;
  onChange: (batch: boolean) => void;
}) {
  const name = useId();
  const batch = preference?.batch_verification ?? true;
  const options = [
    { batch: true, label: "Batch the reviewers — half price", detail: 'Progress is a count, like “0 of 121 seats returned”.' },
    { batch: false, label: "Stream the reviewers — full price", detail: "Each seat shows as it works, like the five lenses." },
  ];
  return (
    <fieldset data-capability="qc.run" disabled={!preference || pending} className="space-y-2">
      <legend className="mb-2 text-xs font-semibold text-ink">Final QC phase 2</legend>
      {!preference && <p className="text-xs text-ink-faint">Loading the Final QC choice…</p>}
      {options.filter((option) => preference && (!preference.locked || option.batch === batch)).map((option) => (
        <label key={String(option.batch)} className="flex cursor-pointer items-start gap-2 rounded-lg border border-edge p-3 text-xs text-ink-dim">
          <input type="radio" name={name} checked={option.batch === batch} disabled={preference?.locked} onChange={() => onChange(option.batch)} className="mt-0.5 accent-[var(--color-accent)]" />
          <span><span className="font-medium text-ink">{option.label}</span><span className="mt-1 block">{option.detail}</span></span>
        </label>
      ))}
      {preference?.locked ? <p className="text-xs text-ink-faint">Locked by BUILD_A_SPEC_QC_BATCH_VERIFICATION.</p> : <p className="text-xs text-ink-faint">Remembered on this computer. Applies to the next review.</p>}
      {error && <p role="alert" className="text-xs text-warn">{error}</p>}
    </fieldset>
  );
}
