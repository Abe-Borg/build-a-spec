## Install (Windows)

1. Download **BuildASpecSetup.exe** below and run it.
2. The app is not code-signed, so Windows SmartScreen shows
   **"Windows protected your PC"** on first run — click
   **More info → Run anyway**. This is expected; downloads are
   SHA-256-verified against `latest.json` before the in-app
   updater ever launches a future update.
3. No Python, Node, or other tooling is required — everything is
   bundled. The installer adds the Microsoft Edge WebView2
   runtime automatically if your machine doesn't already have it
   (current Windows 10/11 already do).

After installing, launch **Build-a-Spec** from the Start menu and
enter your Anthropic API key when prompted.

Updating from an earlier version? The release notes above also open
inside the app the first time you run it, and Settings can reopen
them any time.

`latest.json` is the update manifest consumed by the built-in
updater — it is not something you need to download.
