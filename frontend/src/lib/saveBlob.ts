/**
 * Hand a Blob to the browser's save path under `filename`.
 *
 * The one anchor-click save in the app. It used to be copy-pasted four
 * times (the project file, the project brief, the Final QC report, and the
 * figure downloads), each with its own comment about the same deferred
 * revocation — and two of the copies had drifted apart on the filename
 * regex. Every download now goes through here: `downloadAttachment` in
 * lib/api.ts for anything the server streams, `lib/figures.ts` for blobs
 * built client-side.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Defer revocation: some browsers consume the object URL asynchronously,
  // so revoking synchronously after click() can cancel the download — which
  // for the project file would let the caller reset/load and lose the
  // session with nothing saved.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
