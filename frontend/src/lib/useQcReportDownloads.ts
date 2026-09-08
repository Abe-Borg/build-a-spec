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
 *
 * The state machine itself is `useDownloads`, shared with the Export menu;
 * this keeps the report surfaces' `(format, runId)` signature.
 */
import { useCallback } from "react";

import { downloadQcReport } from "./api";
import { useDownloads } from "./useDownloads";

export type QcReportFormat = "docx" | "json";

export function useQcReportDownloads(): {
  busy: QcReportFormat | null;
  error: string;
  download: (format: QcReportFormat, runId: unknown) => Promise<void>;
} {
  const downloads = useDownloads<QcReportFormat>();
  const run = downloads.download;
  const download = useCallback(
    (format: QcReportFormat, runId: unknown) =>
      run(format, () => downloadQcReport(format, runId)),
    [run],
  );
  return { busy: downloads.busy, error: downloads.error, download };
}
