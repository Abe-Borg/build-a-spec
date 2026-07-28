/**
 * Routes every external hyperlink click to the user's system browser instead
 * of letting the native pywebview shell navigate its own window away from
 * the app — there is no way back from that. A single document-level,
 * capture-phase listener covers every renderer of an `<a>` (chat markdown
 * links, citation/source lists, the trust dossier, the update banner, ...)
 * without touching each one individually. Same-origin links (downloads,
 * in-app navigation) are left untouched.
 */

function externalHttpUrl(href: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(href, window.location.href);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (parsed.origin === window.location.origin) return null;
  return parsed.href;
}

/** Installs the handler; call the returned function to remove it. */
export function installExternalLinkHandler(): () => void {
  const onClick = (event: MouseEvent) => {
    if (event.defaultPrevented) return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    const anchor = target.closest("a[href]");
    if (!(anchor instanceof HTMLAnchorElement)) return;
    const url = externalHttpUrl(anchor.getAttribute("href") ?? "");
    if (!url) return;
    event.preventDefault();
    const api = window.pywebview?.api;
    if (api?.open_external_link) {
      void api.open_external_link(url);
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  };
  document.addEventListener("click", onClick, true);
  return () => document.removeEventListener("click", onClick, true);
}
