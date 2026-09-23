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
& .\.venv\Scripts\python.exe -m pytest -q tests\test_docx_visual_regression.py
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
& .\.venv\Scripts\python.exe -m pytest -q tests\test_docx_visual_regression.py
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

## Resolve mode: real Word as the redline judge

The same bridge has a second mode. With `--resolve`, the owned, hidden Word
opens a DOCX read-only, runs **Accept All Changes** or **Reject All
Changes** (or neither — `resave`, a plain re-save), and saves the result as a
new DOCX in Word's default format. Every rule above still holds: hidden STA
PowerShell, alerts and macros off before anything opens, read-only and off
Recent Files (the saved copy too), the ownership handshake, and cleanup of
only the Word it proved it started. It never overwrites: an output that
already exists is refused. It needs no rasterizer, so it runs from the repo
virtual environment:

```powershell
.\.venv\Scripts\python tools\render_docx_word.py --resolve accept --output .\artifacts\accepted.docx .\artifacts\redline.docx
```

It prints how many tracked changes Word read, by whom, and how many were
left once the action ran.

The redline judge (`tests/test_redline_word_judge.py`) drives it a batch at a
time: one owned Word per group of files, each file its own job, and a file
Word cannot open, resolve or save fails only its own job. It collects as
clean skips unless asked. Your own Word windows can stay open (the bridge
only ever touches the Word it started), but do not start a new one while the
judge runs: two new Word processes appearing at once fail that group's
ownership check.

```powershell
$env:BUILD_A_SPEC_WORD_JUDGE = "1"
.\.venv\Scripts\python -m pytest -q tests\test_redline_word_judge.py
```

`BUILD_A_SPEC_WORD_EXECUTABLE` and `BUILD_A_SPEC_WORD_TIMEOUT` apply as above;
the timeout is per document, so a batch's ceiling is that times its jobs. The
results land in `artifacts\word-judge\` (`BUILD_A_SPEC_WORD_JUDGE_DIR` moves
it): `report.json`, rewritten after every group, and every file Word was
handed and saved, by run, group and case. What the judge proves and the four
things it tolerates are in [DOCX_FIDELITY.md](DOCX_FIDELITY.md) → *Real Word
as the judge*.
