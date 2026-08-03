/**
 * Busy/error state for the Final QC report downloads (QCDrawer +
 * QCReportModal — the only two surfaces that offer them).
 *
 * The reports used to be bare `<a download>` links, which have no failure
 * mode a user can see: a 409 (the backend's selected report changed under
 * the pinned run id) or a 500 downloads nothing and says nothing,
 * especially in the native shell. Routing the click through
 * `downloadQcReport` shows a preparing state while the artifact is fetched
 * and surfaces the server's exact error message beside the button.
 */
import { useCallback, useState } from "react";

import { downloadQcReport } from "./api";

export type QcReportFormat = "docx" | "json";

export function useQcReportDownloads(): {
  busy: QcReportFormat | null;
  error: string;
  download: (format: QcReportFormat, runId: unknown) => Promise<void>;
} {
  const [busy, setBusy] = useState<QcReportFormat | null>(null);
  const [error, setError] = useState("");
  const download = useCallback(
    async (format: QcReportFormat, runId: unknown) => {
      setBusy(format);
      setError("");
      try {
        await downloadQcReport(format, runId);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(null);
      }
    },
    [],
  );
  return { busy, error, download };
}
