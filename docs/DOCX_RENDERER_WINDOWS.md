# Windows DOCX renderer setup

The optional DOCX visual-regression suite can run against either LibreOffice or
Microsoft Word. In both cases, use the bundled Python runtime for rendering and
the repo virtual environment to run pytest.

## LibreOffice

Configure the repo-local compatibility entrypoint so LibreOffice receives a
valid user-profile URI:

```powershell
$env:BUILD_A_SPEC_DOCX_RENDERER = (Resolve-Path .\tools\render_docx_windows_compat.py).Path
$env:BUILD_A_SPEC_CANONICAL_DOCX_RENDERER = 'C:\path\to\documents\render_docx.py'
$env:BUILD_A_SPEC_RENDER_PYTHON = 'C:\path\to\bundled\python.exe'
# Set this only when soffice is not already on PATH:
$env:BUILD_A_SPEC_RENDER_EXTRA_PATH = 'C:\path\to\LibreOffice\program'
```

Then run the visual tests normally:

```powershell
& .\venv\Scripts\python.exe -m pytest -q tests\test_docx_visual_regression.py
```

The wrapper preserves every canonical CLI argument. It changes only a soffice
argument shaped like `-env:UserInstallation=file://C:\...`, converting it to
`-env:UserInstallation=file:///C:/...`. The test harness automatically adds the
bundled Poppler native directory when it can derive it from the configured
Python path. LibreOffice still needs to be installed or otherwise available as
`soffice` on `PATH`.

## Microsoft Word

The Word entrypoint renders through a dedicated hidden Word process, then uses
the bundled `pdf2image`/Pillow stack for PNG output:

```powershell
$env:BUILD_A_SPEC_DOCX_RENDERER = (Resolve-Path .\tools\render_docx_word.py).Path
$env:BUILD_A_SPEC_RENDER_PYTHON = 'C:\path\to\bundled\python.exe'
$env:BUILD_A_SPEC_WORD_EXECUTABLE = 'C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE'
& .\venv\Scripts\python.exe -m pytest -q tests\test_docx_visual_regression.py
```

The automation bridge runs Windows PowerShell in STA mode with no visible
window, disables Office alerts and macros before opening the DOCX, and opens it
read-only without adding it to Recent Files. It records the new WINWORD PID and
start time plus a random ownership token and executable path, refuses a
pre-existing process, and only quits or force-cleans the instance it proved it
created. Page images and the optional PDF are staged as one managed set: a
failed publication restores the prior set, while a successful shorter render
removes stale `page-N.png` files (and removes a prior PDF when `--emit_pdf` is
not requested). `BUILD_A_SPEC_WORD_TIMEOUT` optionally overrides the 120-second
internal Word conversion timeout; keep it below the harness's
`BUILD_A_SPEC_RENDER_TIMEOUT` (240 seconds by default).

If the automation host is externally terminated before COM activation returns,
there is no exact process identity available for forced cleanup. The renderer
deliberately does not guess at a `WINWORD` process, so it cannot risk stopping a
user-owned Word session.
