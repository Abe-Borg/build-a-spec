import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { installClientDiagnostics } from "./lib/clientLog";
import { bootstrapDesktopApi } from "./lib/desktopSecurity";

const rootElement = document.getElementById("root")!;

async function start(): Promise<void> {
  // Authentication must exist before diagnostics or App can issue /api calls.
  await bootstrapDesktopApi();
  installClientDiagnostics();
  createRoot(rootElement).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void start().catch(() => {
  // Deliberately generic: no nonce/token/error object reaches console or DOM.
  rootElement.textContent =
    "Build-a-Spec could not establish its secure local connection. Close the app and try again.";
  rootElement.setAttribute("role", "alert");
});
