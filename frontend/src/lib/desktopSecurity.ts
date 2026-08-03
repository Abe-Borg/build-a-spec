/** Per-launch authentication for the local desktop API.
 *
 * The boot nonce arrives in a URL fragment (never an HTTP request/log). It is
 * exchanged once for an in-memory header token plus an HttpOnly Strict cookie.
 * The closure below is the token's only JavaScript owner; it is never exported,
 * logged, persisted, or placed back into a URL.
 */

const BOOT_FRAGMENT_KEY = "buildaspec_boot";
const BOOT_HEADER = "X-BuildASpec-Boot-Nonce";
const TOKEN_HEADER = "X-BuildASpec-Token";

type HealthIdentity = {
  status?: unknown;
  model?: unknown;
  api_key_present?: unknown;
  workspace_id?: unknown;
  workspace_scope?: unknown;
  generation?: unknown;
  boot_nonce_fingerprint?: unknown;
};

type BootstrapPayload = {
  ok?: unknown;
  api_token?: unknown;
  boot_nonce?: unknown;
};

function fragmentBootNonce(): string {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  return params.get(BOOT_FRAGMENT_KEY)?.trim() ?? "";
}

function removeBootFragment(): void {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  params.delete(BOOT_FRAGMENT_KEY);
  const remaining = params.toString();
  const next = `${window.location.pathname}${window.location.search}${remaining ? `#${remaining}` : ""}`;
  window.history.replaceState(null, "", next);
}

function isFullHealth(identity: HealthIdentity): boolean {
  return (
    identity.status === "ok" &&
    typeof identity.model === "string" &&
    typeof identity.api_key_present === "boolean" &&
    typeof identity.workspace_id === "number" &&
    (identity.workspace_scope === "original" ||
      identity.workspace_scope === "tutorial" ||
      identity.workspace_scope === "scenario") &&
    typeof identity.generation === "number"
  );
}

function installApiFetchToken(
  nativeFetch: typeof window.fetch,
  apiToken: string,
): void {
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const rawUrl =
      typeof input === "string" || input instanceof URL
        ? String(input)
        : input.url;
    const url = new URL(rawUrl, window.location.href);
    if (
      url.origin !== window.location.origin ||
      !(url.pathname === "/api" || url.pathname.startsWith("/api/"))
    ) {
      return nativeFetch(input, init);
    }

    const inherited = input instanceof Request ? input.headers : undefined;
    const headers = new Headers(init?.headers ?? inherited);
    headers.set(TOKEN_HEADER, apiToken);
    const credentials =
      init?.credentials ??
      (input instanceof Request ? input.credentials : "same-origin");
    return nativeFetch(input, { ...init, credentials, headers });
  };
}

/** Bootstrap before diagnostics listeners or React render. */
export async function bootstrapDesktopApi(): Promise<void> {
  const nativeFetch = window.fetch.bind(window);
  const bootNonce = fragmentBootNonce();

  if (!bootNonce) {
    // Explicit ASGI/dev-server launches are intentionally unsecured, and a
    // reloaded authenticated desktop page has its HttpOnly session cookie.
    // Both return full health. A secure unauthenticated listener returns only
    // a one-way launch fingerprint, which cannot bootstrap and must fail
    // closed instead of being mistaken for the normal Health contract.
    const health = await nativeFetch("/api/health", {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!health.ok) throw new Error("desktop bootstrap health failed");
    const identity = (await health.json()) as HealthIdentity;
    if (isFullHealth(identity)) return;
    throw new Error("desktop bootstrap capability unavailable");
  }

  const response = await nativeFetch("/api/bootstrap", {
    method: "GET",
    headers: { [BOOT_HEADER]: bootNonce },
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("desktop bootstrap rejected");
  const payload = (await response.json()) as BootstrapPayload;
  if (
    payload.ok !== true ||
    typeof payload.api_token !== "string" ||
    payload.api_token.length < 32 ||
    payload.boot_nonce !== bootNonce
  ) {
    throw new Error("desktop bootstrap identity mismatch");
  }

  installApiFetchToken(nativeFetch, payload.api_token);
  removeBootFragment();
}
