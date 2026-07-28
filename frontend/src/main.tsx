import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { installClientDiagnostics } from "./lib/clientLog";

// Before the root renders, so even a first-paint crash is reported.
installClientDiagnostics();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
