"""Third-party license notices for the Windows build — importable, unit-tested.

Copied from Claude-Spec-Critic ``packaging/windows/third_party_notices.py``
and adapted for Build-a-Spec: identity strings, Tcl/Tk only when the analysis
actually bundles tkinter (Build-a-Spec's UI is a WebView2 window, not Tk), a
required supplement for Microsoft's WebView2 SDK binaries that pywebview's
wheel ships without their license, and the JavaScript packages the React
frontend is built from.

Why this exists: the PyInstaller build bundles the Python interpreter, every
runtime Python package with its transitive dependencies, and the built React
frontend, which carries the code of its npm dependencies. Their licenses (MIT,
BSD, Apache-2.0, ISC, ...) require their copyright and license text to travel
with a binary distribution, and ``BuildASpecSetup.exe`` carried none of them:
the only license files that reached ``dist/BuildASpec/`` were the ones inside
the few ``.dist-info`` folders the spec copies for runtime metadata reads. This
module writes one ``THIRD-PARTY-NOTICES.txt`` holding all of them;
``installer.iss`` installs it beside the app.

What counts as bundled is decided by the build, not by what happens to be
installed on the build machine:

* ``build-a-spec.spec`` calls :func:`write_third_party_notices` right after
  ``Analysis``, passing every table of files the analysis collected
  (``a.pure``, ``a.scripts``, ``a.binaries``, ``a.datas``). A distribution is
  bundled when one of those source files is listed in its ``RECORD``, so the
  notices name what ships: PyInstaller's own build-time helpers (altgraph,
  pefile, pip, ...) are left out unless a file of theirs is collected.
  PyInstaller itself is always listed, because its bootloader is
  ``BuildASpec.exe``. Build-a-Spec's own license ships as ``LICENSE.txt``.
* Each bundled distribution contributes the license files it ships: the ones
  its metadata declares (``License-File``), any other LICENSE / LICENCE /
  COPYING / NOTICE / COPYRIGHT file in its ``.dist-info``, and license-named
  files elsewhere in its ``RECORD`` (third-party code vendored inside the
  package). A multi-line ``License`` metadata field, which some packages use
  for the full text, counts when no file does.
* A distribution that vendors someone else's binaries WITHOUT shipping their
  license in its wheel is listed in :data:`REQUIRED_SUPPLEMENTS`; its
  supplement text is required whenever it is bundled. Today that is pywebview,
  whose ``webview/lib`` holds Microsoft's WebView2 SDK DLLs
  (``Microsoft.Web.WebView2.Core.dll``, ``...WinForms.dll``,
  ``WebView2Loader.dll``). pywebview's source distribution carries their
  license as ``webview/lib/Microsoft.Web.WebView2.LICENSE.md``; the wheel does
  not.
* The interpreter contributes ``LICENSE.txt`` from its install directory
  (``sys.base_prefix``), reproduced whole. python.org's Windows build writes
  that file by appending the terms of the libraries it ships to the PSF
  license: the MSVC runtime notice, bzip2, libffi, OpenSSL, Tcl, Tk, and Tix
  (CPython's ``PCbuild/regen.targets``).
* Tcl and Tk are listed only when the analysis collected the ``_tkinter``
  extension. Nothing in Build-a-Spec imports tkinter, so normally they are
  not; if a dependency ever starts to, their ``license.terms`` become
  required, from the library directories PyInstaller bundles
  (``PyInstaller.utils.hooks.tcl_tk.tcltk_info``), else from the
  interpreter's ``LICENSE.txt``, which holds them verbatim.
* The frontend contributes every production package in
  ``frontend/package-lock.json`` (everything not marked ``dev``), read from
  ``frontend/node_modules`` — the tree ``npm ci`` installs and ``npm run
  build`` bundles from. That is a superset of what Vite's tree-shaking keeps,
  on purpose: a license listed for code that was shaken out costs nothing,
  and one missing for code that shipped is a violation. An ``optional``
  package that is not installed is skipped (npm left it out on this
  platform, so the build could not have bundled it).

The module is deliberately strict: a bundled distribution or npm package with
no license text, a required supplement that is missing, an interpreter without
its license file, or (when bundled) Tcl/Tk terms that cannot be found raise
:class:`NoticesError` and fail the build, naming every gap at once, and no
notices file is written. An installer that silently leaves a license out must
never ship. When a package really ships no license text, the remedy is a
release that does, or the text, taken from that project's source at exactly
that version, saved next to this file as
``third_party_licenses/<name>-<version>.txt`` (``npm-<name>-<version>.txt``
for an npm package, with a scoped name's ``/`` written as ``+``). The version
in the file name makes an upgrade fail again until someone checks the new
text. The same holds for a supplement:
``third_party_licenses/<name>-<version>-<suffix>.txt``.

Run directly (``python packaging/windows/third_party_notices.py``) to check,
before the long PyInstaller build, that this environment's interpreter license
and the frontend's npm license texts can be found; exit status 1 on any
problem. ``--frontend-only`` checks the npm texts alone (CI's frontend job). Which Python distributions are bundled is known only after the
analysis, so that check runs inside the spec.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import platform
import re
import sys
import sysconfig
from importlib import metadata
from typing import Iterable, Sequence

#: The notices document. The spec writes it into PyInstaller's ``dist/``
#: folder, next to (not inside) the one-folder build, and ``installer.iss``
#: installs it beside ``BuildASpec.exe``.
NOTICES_FILENAME = "THIRD-PARTY-NOTICES.txt"

#: Listed whatever the analysis collected: PyInstaller's bootloader *is*
#: ``BuildASpec.exe``, and it is not a file of the analysis tables.
ALWAYS_BUNDLED = ("pyinstaller",)
#: Never listed: Build-a-Spec's own license ships as ``LICENSE.txt``. It is
#: not an installed distribution today; the name guards a future pyproject.
FIRST_PARTY = ("build-a-spec",)

_PACKAGING_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGING_DIR.parent.parent

#: License texts for bundled packages that ship none, one file per release:
#: ``<canonical name>-<version>.txt`` (``npm-...`` for npm packages), plus the
#: required supplements below. See the module docstring.
LICENSE_OVERRIDES_DIR = _PACKAGING_DIR / "third_party_licenses"

#: Bundled distributions that vendor third-party binaries whose license their
#: wheel does not ship: canonical name -> (what the supplement covers, file
#: suffix). The text is required whenever the distribution is bundled, at
#: ``third_party_licenses/<name>-<version>-<suffix>.txt``.
REQUIRED_SUPPLEMENTS: dict[str, tuple[str, str]] = {
    "pywebview": (
        "Microsoft Edge WebView2 SDK (Microsoft.Web.WebView2.Core.dll, "
        "Microsoft.Web.WebView2.WinForms.dll, WebView2Loader.dll), vendored in "
        "webview/lib; text from webview/lib/Microsoft.Web.WebView2.LICENSE.md "
        "in pywebview's source distribution",
        "webview2-sdk",
    ),
}

#: The frontend's lockfile and install tree (``npm ci`` in ``frontend/``).
FRONTEND_DIR = _REPO_ROOT / "frontend"

#: Heading every CPython license file carries; checked so that some other
#: ``LICENSE.txt`` in the install directory is never passed off as Python's.
PSF_LICENSE_MARKER = "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2"

#: Tcl 8.6's and Tk 8.6's license terms open by naming their copyright
#: holders, and the two lists differ only by Apple Inc.; both end with the same
#: sentence. Matched with any whitespace between words, because the
#: interpreter's ``LICENSE.txt`` has CRLF line endings.
TCL_TERMS_OPENING = (
    "This software is copyrighted by the Regents of the University of "
    "California, Sun Microsystems, Inc., Scriptics Corporation, ActiveState "
    "Corporation and other parties."
)
TK_TERMS_OPENING = (
    "This software is copyrighted by the Regents of the University of "
    "California, Sun Microsystems, Inc., Scriptics Corporation, ActiveState "
    "Corporation, Apple Inc. and other parties."
)
TCL_TK_TERMS_CLOSING = "terms specified in this license."

_LICENSE_NAME_RE = re.compile(r"^(licen[cs]e|copying|notice|copyright)", re.IGNORECASE)
# A module named license.py (or a compiled one) is code, not a license text.
_NOT_TEXT_SUFFIXES = frozenset(
    {".py", ".pyc", ".pyo", ".pyi", ".pyd", ".so", ".dll", ".dylib", ".exe",
     ".js", ".mjs", ".cjs", ".ts", ".map"}
)
_TCL_TK_DIR_RE = re.compile(r"^(?:tcl|tk)(\d+(?:\.\d+)*)$", re.IGNORECASE)
_TKINTER_EXT_RE = re.compile(r"^_tkinter(?:\.|$)", re.IGNORECASE)
_RULE = "=" * 78


class NoticesError(RuntimeError):
    """A bundled component's license text cannot be found; the build must stop."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__(
            "The third-party notices are incomplete, so the build stops (a "
            "bundled binary distribution must carry every bundled component's "
            "license text):\n"
            + "\n".join(f"  - {p}" for p in self.problems)
        )


@dataclasses.dataclass(frozen=True)
class LicenseText:
    """One license text and where it came from, as the notices show it."""

    source: str
    text: str


@dataclasses.dataclass(frozen=True)
class Component:
    """A bundled component: its name, version, license, and full texts.

    ``kind`` groups the notices: ``runtime`` (the interpreter, Tcl/Tk),
    ``python`` (a Python distribution) or ``npm`` (a frontend package).
    """

    name: str
    version: str
    license: str
    texts: tuple[LicenseText, ...]
    note: str = ""
    kind: str = "python"

    @property
    def title(self) -> str:
        return f"{self.name} {self.version}".strip()


def canonical_name(name: str) -> str:
    """PEP 503 normalization: ``Jaraco.Classes`` and ``jaraco_classes`` match."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalize_path(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _read_text(path: str | os.PathLike[str]) -> str | None:
    """File text, decoded and with LF line endings; None when blank or unreadable.

    Leading blank lines and trailing whitespace are dropped; everything else is
    kept as written (a centered heading keeps its indentation). A file that is
    not UTF-8 is read as Latin-1, which decodes any byte, rather than dropped.
    """
    try:
        data = pathlib.Path(path).read_bytes()
    except OSError:
        return None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\n").rstrip()
    return text or None


def _flexible(phrase: str) -> str:
    """Regex for ``phrase`` with any run of whitespace between its words."""
    return r"\s+".join(re.escape(word) for word in phrase.split())


def _is_license_filename(name: str) -> bool:
    return bool(_LICENSE_NAME_RE.match(name)) and (
        os.path.splitext(name)[1].lower() not in _NOT_TEXT_SUFFIXES
    )


# ---------------------------------------------------------------------------
# Which distributions are bundled
# ---------------------------------------------------------------------------


def source_paths(tables: Iterable[Iterable[Sequence[object]]]) -> list[str]:
    """Source paths from PyInstaller's ``(dest, source, typecode)`` tables."""
    paths = []
    for table in tables:
        for entry in table:
            if len(entry) >= 2 and isinstance(entry[1], str) and entry[1]:
                paths.append(entry[1])
    return paths


def tkinter_bundled(sources: Iterable[str]) -> bool:
    """True when the analysis collected the ``_tkinter`` extension module."""
    return any(_TKINTER_EXT_RE.match(os.path.basename(p)) for p in sources)


def bundled_distributions(
    sources: Iterable[str],
    distributions: Iterable[metadata.Distribution] | None = None,
    *,
    always: Sequence[str] = ALWAYS_BUNDLED,
    first_party: Sequence[str] = FIRST_PARTY,
) -> list[metadata.Distribution]:
    """The installed distributions that own at least one bundled source file.

    A file is owned by the distribution whose ``RECORD`` lists it. When a name
    is installed twice on the path, the first copy wins, as it does for
    imports. ``always`` is added whatever was collected (it must be
    installed), ``first_party`` is removed. Sorted by canonical name.
    """
    dists: dict[str, metadata.Distribution] = {}
    owners: dict[str, str] = {}
    for dist in metadata.distributions() if distributions is None else distributions:
        name = dist.metadata.get("Name")
        if not name:
            continue
        key = canonical_name(name)
        if key in dists:
            continue
        dists[key] = dist
        for file in dist.files or ():
            owners.setdefault(_normalize_path(dist.locate_file(file)), key)

    bundled = {owners[p] for p in map(_normalize_path, sources) if p in owners}
    missing = [n for n in always if canonical_name(n) not in dists]
    if missing:
        raise NoticesError(
            [
                f"{n} is not installed in this environment, but its code is always "
                "bundled (PyInstaller's bootloader is BuildASpec.exe)"
                for n in missing
            ]
        )
    bundled |= {canonical_name(n) for n in always}
    bundled -= {canonical_name(n) for n in first_party}
    return [dists[key] for key in sorted(bundled)]


# ---------------------------------------------------------------------------
# A distribution's license
# ---------------------------------------------------------------------------


def license_identifier(dist: metadata.Distribution) -> str:
    """The license a distribution declares: expression, short field, or classifiers."""
    meta = dist.metadata
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return expression
    field = (meta.get("License") or "").strip()
    if field and "\n" not in field and len(field) <= 200 and field.upper() != "UNKNOWN":
        return field
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or ()
        if c.startswith("License ::")
    ]
    if classifiers:
        return "; ".join(classifiers)
    # A multi-line License field is the full text; its first line names it.
    first = field.splitlines()[0].strip() if field else ""
    if first and len(first) <= 80 and first.upper() != "UNKNOWN":
        return first
    return "not declared in its package metadata (see the text below)"


def _metadata_dir(files: Sequence[metadata.PackagePath]) -> str | None:
    for file in files:
        parts = file.parts
        if len(parts) >= 2 and (
            (parts[-1] == "METADATA" and parts[-2].endswith(".dist-info"))
            or (parts[-1] == "PKG-INFO" and parts[-2].endswith(".egg-info"))
        ):
            return "/".join(parts[:-1])
    return None


def _is_license_name(file: metadata.PackagePath) -> bool:
    name = file.parts[-1] if file.parts else ""
    return _is_license_filename(name) and "__pycache__" not in file.parts


def _override_source(name: str) -> str:
    return (
        f"packaging/windows/third_party_licenses/{name} "
        "(supplied with Build-a-Spec; the package ships no license file)"
    )


def distribution_license_texts(
    dist: metadata.Distribution,
    *,
    overrides_dir: str | os.PathLike[str] | None = LICENSE_OVERRIDES_DIR,
) -> tuple[LicenseText, ...]:
    """Every license text ``dist`` ships, in order; empty when it ships none.

    Order: the files its ``License-File`` metadata declares (under
    ``.dist-info/licenses/`` in metadata 2.4, directly in ``.dist-info`` in
    older wheels), then any other license-named file in its ``.dist-info``,
    then license-named files elsewhere in its ``RECORD`` (vendored code). With
    none of those, a multi-line ``License`` field, and after that an override
    file from ``overrides_dir``. Required supplements are added by
    :func:`distribution_components`, not here.
    """
    files = list(dist.files or ())
    by_path = {file.as_posix(): file for file in files}
    meta_dir = _metadata_dir(files)

    chosen: list[metadata.PackagePath] = []
    if meta_dir is not None:
        for declared in dist.metadata.get_all("License-File") or ():
            for candidate in (f"{meta_dir}/licenses/{declared}", f"{meta_dir}/{declared}"):
                file = by_path.get(candidate)
                if file is not None:
                    if file not in chosen:
                        chosen.append(file)
                    break
    own, vendored = [], []
    for file in sorted(files, key=lambda f: f.as_posix()):
        if file in chosen or not _is_license_name(file):
            continue
        in_meta = meta_dir is not None and file.as_posix().startswith(meta_dir + "/")
        (own if in_meta else vendored).append(file)

    texts = []
    for file in [*chosen, *own, *vendored]:
        text = _read_text(dist.locate_file(file))
        if text:
            texts.append(LicenseText(file.as_posix(), text))
    if texts:
        return tuple(texts)

    field = (dist.metadata.get("License") or "").strip()
    if len([line for line in field.splitlines() if line.strip()]) >= 3:
        return (LicenseText("the License field of its package metadata", field),)

    if overrides_dir is not None:
        name = canonical_name(dist.metadata.get("Name") or "")
        override = pathlib.Path(overrides_dir) / f"{name}-{dist.version}.txt"
        text = _read_text(override)
        if text:
            return (LicenseText(_override_source(override.name), text),)
    return ()


def supplement_path(
    name: str,
    version: str,
    overrides_dir: str | os.PathLike[str] | None = LICENSE_OVERRIDES_DIR,
) -> pathlib.Path | None:
    """Where a required supplement for ``name`` at ``version`` lives, or None."""
    entry = REQUIRED_SUPPLEMENTS.get(canonical_name(name))
    if entry is None:
        return None
    base = pathlib.Path(overrides_dir) if overrides_dir is not None else LICENSE_OVERRIDES_DIR
    return base / f"{canonical_name(name)}-{version}-{entry[1]}.txt"


def distribution_components(
    dists: Iterable[metadata.Distribution],
    *,
    overrides_dir: str | os.PathLike[str] | None = LICENSE_OVERRIDES_DIR,
) -> list[Component]:
    """One component per distribution; raises naming every one without a text."""
    components, problems = [], []
    for dist in dists:
        name = dist.metadata.get("Name") or "?"
        texts = distribution_license_texts(dist, overrides_dir=overrides_dir)
        if not texts:
            where = (
                pathlib.Path(overrides_dir) / "<name>-<version>.txt"
                if overrides_dir is not None
                else "packaging/windows/third_party_licenses/<name>-<version>.txt"
            )
            problems.append(
                f"{name} {dist.version} is bundled but ships no license text (no "
                "license file in its RECORD and no full text in its License "
                "metadata). Upgrade to a release that ships one, or save the text "
                f"from that project's source at exactly this version as {where}"
            )
            continue
        extra = supplement_path(name, dist.version, overrides_dir)
        if extra is not None:
            covers = REQUIRED_SUPPLEMENTS[canonical_name(name)][0]
            text = _read_text(extra)
            if not text:
                problems.append(
                    f"{name} {dist.version} vendors third-party binaries whose license "
                    f"its wheel does not ship: {covers}. Save that text, taken from "
                    f"{name}'s source at exactly this version, as {extra}"
                )
                continue
            texts = (
                *texts,
                LicenseText(
                    f"packaging/windows/third_party_licenses/{extra.name} "
                    f"(supplied with Build-a-Spec: {covers})",
                    text,
                ),
            )
        components.append(Component(name, dist.version, license_identifier(dist), texts))
    if problems:
        raise NoticesError(problems)
    return components


# ---------------------------------------------------------------------------
# The frontend's npm packages
# ---------------------------------------------------------------------------


def _npm_name(lock_key: str, entry: dict) -> str:
    """A package's name: the lockfile's ``name``, else the path after the last
    ``node_modules/`` (which keeps a scope: ``@dnd-kit/core``)."""
    if isinstance(entry.get("name"), str) and entry["name"]:
        return entry["name"]
    return lock_key.rsplit("node_modules/", 1)[-1]


def _npm_override_name(name: str, version: str) -> str:
    return f"npm-{name.replace('/', '+')}-{version}.txt"


def frontend_components(
    frontend_dir: str | os.PathLike[str] = FRONTEND_DIR,
    *,
    overrides_dir: str | os.PathLike[str] | None = LICENSE_OVERRIDES_DIR,
) -> list[Component]:
    """One component per production npm package; raises naming every gap.

    Production means every ``package-lock.json`` entry not marked ``dev`` or
    ``devOptional`` (the root entry, Build-a-Spec itself, excluded). Each
    contributes the license-named files at its package root, read from
    ``node_modules``; with none, an override file. Sorted by name, then
    version.
    """
    root = pathlib.Path(frontend_dir)
    lock_path = root / "package-lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NoticesError(
            [f"the frontend lockfile {lock_path} cannot be read ({exc})"]
        ) from exc
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise NoticesError(
            [f"{lock_path} has no 'packages' table (lockfileVersion 2 or later is required)"]
        )

    components, problems = [], []
    for key, entry in sorted(packages.items()):
        if not key or not isinstance(entry, dict):
            continue
        if entry.get("dev") or entry.get("devOptional") or entry.get("link"):
            continue
        name = _npm_name(key, entry)
        version = str(entry.get("version") or "")
        package_dir = root / key
        if not package_dir.is_dir():
            if entry.get("optional"):
                continue
            problems.append(
                f"npm package {name} {version} is a production dependency but is not "
                f"installed at {package_dir}; run `npm ci` in frontend/ before building"
            )
            continue
        texts = []
        for child in sorted(package_dir.iterdir(), key=lambda p: p.name.lower()):
            if child.is_file() and _is_license_filename(child.name):
                text = _read_text(child)
                if text:
                    texts.append(LicenseText(f"{key}/{child.name}", text))
        if not texts and overrides_dir is not None:
            override = pathlib.Path(overrides_dir) / _npm_override_name(name, version)
            text = _read_text(override)
            if text:
                texts.append(LicenseText(_override_source(override.name), text))
        if not texts:
            where = (
                pathlib.Path(overrides_dir) / _npm_override_name(name, version)
                if overrides_dir is not None
                else f"packaging/windows/third_party_licenses/{_npm_override_name(name, version)}"
            )
            problems.append(
                f"npm package {name} {version} is a production dependency but ships "
                "no license file. Upgrade to a release that ships one, or save the "
                f"text from that project's source at exactly this version as {where}"
            )
            continue
        declared = entry.get("license")
        if not isinstance(declared, str) or not declared.strip():
            try:
                manifest = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
                declared = manifest.get("license")
            except (OSError, ValueError):
                declared = None
        license_id = (
            declared.strip()
            if isinstance(declared, str) and declared.strip()
            else "not declared in its package.json (see the text below)"
        )
        components.append(
            Component(name, version, license_id, tuple(texts), kind="npm")
        )
    if problems:
        raise NoticesError(problems)
    components.sort(key=lambda c: (c.name.lower(), c.version))
    return components


# ---------------------------------------------------------------------------
# The interpreter and Tcl/Tk
# ---------------------------------------------------------------------------


def interpreter_license_path(
    base_prefix: str | os.PathLike[str] | None = None,
    stdlib_dir: str | os.PathLike[str] | None = None,
) -> pathlib.Path:
    """The license file of the interpreter being bundled.

    ``LICENSE.txt`` in ``sys.base_prefix`` is where python.org's Windows
    install keeps it (and the base prefix, not a virtualenv's, is the install
    PyInstaller copies the interpreter from); the standard-library directory is
    where POSIX builds keep it.
    """
    base = pathlib.Path(sys.base_prefix if base_prefix is None else base_prefix)
    stdlib = pathlib.Path(sysconfig.get_path("stdlib") if stdlib_dir is None else stdlib_dir)
    candidates = [base / "LICENSE.txt", base / "LICENSE", stdlib / "LICENSE.txt"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise NoticesError(
        [
            "the interpreter's license file was not found (looked for "
            + ", ".join(str(c) for c in candidates)
            + ")"
        ]
    )


def _tcl_tk_version(library_dir: pathlib.Path) -> str:
    match = _TCL_TK_DIR_RE.match(library_dir.name)
    return match.group(1) if match else ""


def _terms(
    label: str,
    library_dir: pathlib.Path,
    opening: str,
    interpreter_text: str | None,
) -> LicenseText | None:
    """``license.terms`` in the library directory, else the block in Python's license."""
    text = _read_text(library_dir / "license.terms")
    if text:
        return LicenseText(f"license.terms in the bundled {library_dir.name} library directory", text)
    if interpreter_text:
        pattern = _flexible(opening) + r".*?" + _flexible(TCL_TK_TERMS_CLOSING)
        match = re.search(pattern, interpreter_text, re.DOTALL)
        if match:
            return LicenseText(
                f"the {label} license terms in the interpreter's LICENSE.txt "
                f"(the {library_dir.name} library directory has no license file of its own)",
                match.group(0),
            )
    return None


def base_components(
    *,
    include_tcl_tk: bool = False,
    tcl_data_dir: str | os.PathLike[str] | None = None,
    tk_data_dir: str | os.PathLike[str] | None = None,
    interpreter_license: str | os.PathLike[str] | None = None,
    python_version: str | None = None,
) -> list[Component]:
    """The interpreter (and, when bundled, Tcl and Tk); raises naming every gap."""
    problems: list[str] = []
    interpreter_text = None
    try:
        path = (
            interpreter_license_path()
            if interpreter_license is None
            else pathlib.Path(interpreter_license)
        )
    except NoticesError as exc:
        problems += exc.problems
    else:
        interpreter_text = _read_text(path)
        if not interpreter_text:
            problems.append(f"the interpreter's license file {path} is empty or unreadable")
            interpreter_text = None
        elif PSF_LICENSE_MARKER not in interpreter_text:
            problems.append(
                f"{path} is not the Python license (it lacks {PSF_LICENSE_MARKER!r})"
            )
            interpreter_text = None

    components = []
    if interpreter_text:
        components.append(
            Component(
                "Python",
                python_version or platform.python_version(),
                "PSF-2.0",
                (LicenseText("LICENSE.txt from the interpreter's install directory", interpreter_text),),
                note="the bundled interpreter",
                kind="runtime",
            )
        )

    if include_tcl_tk:
        for label, library, opening in (
            ("Tcl", tcl_data_dir, TCL_TERMS_OPENING),
            ("Tk", tk_data_dir, TK_TERMS_OPENING),
        ):
            if library is None:
                problems.append(
                    f"tkinter is bundled but the {label} library directory is unknown, "
                    "so its license terms cannot be located"
                )
                continue
            library_dir = pathlib.Path(library)
            terms = _terms(label, library_dir, opening, interpreter_text)
            if terms is None:
                problems.append(
                    f"the {label} license terms were found neither in "
                    f"{library_dir / 'license.terms'} nor in the interpreter's license file"
                )
                continue
            components.append(
                Component(
                    label,
                    _tcl_tk_version(library_dir),
                    "TCL (the Tcl/Tk license)",
                    (terms,),
                    note="bundled with tkinter",
                    kind="runtime",
                )
            )
    if problems:
        raise NoticesError(problems)
    return components


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

_GROUPS = (
    ("runtime", "Runtime"),
    ("python", "Python packages"),
    ("npm", "JavaScript packages (the user interface)"),
)


def render_notices(components: Sequence[Component]) -> str:
    """The notices document (LF line endings; the writer converts them)."""
    lines = [
        "THIRD-PARTY NOTICES",
        "",
        "Build-a-Spec for Windows is built with PyInstaller and bundles the",
        "components listed below: the Python interpreter, Python packages, and the",
        "JavaScript packages its user interface is built from. Each remains under",
        "its own license; its full text is reproduced in this file as the",
        "component distributes it. Build-a-Spec's own license is in LICENSE.txt,",
        "in this folder.",
        "",
        "When the Microsoft Edge WebView2 Runtime is missing, Setup runs",
        "Microsoft's WebView2 bootstrapper, which downloads and installs the",
        "runtime from Microsoft under Microsoft's own license terms. The runtime",
        "is not part of Build-a-Spec and is not covered by this file.",
    ]
    for kind, heading in _GROUPS:
        members = [c for c in components if c.kind == kind]
        if not members:
            continue
        lines += ["", heading, "-" * len(heading)]
        lines += [f"  {c.title} - {c.license}" for c in members]
    for kind, _heading in _GROUPS:
        for component in (c for c in components if c.kind == kind):
            heading = component.title + (f" ({component.note})" if component.note else "")
            if component.kind == "npm":
                heading += " [npm]"
            lines += ["", "", _RULE, heading, f"License: {component.license}"]
            for text in component.texts:
                lines += ["", f"--- {text.source} ---", "", text.text]
    return "\n".join(lines) + "\n"


def build_notices(
    tables: Iterable[Iterable[Sequence[object]]],
    *,
    tcl_data_dir: str | os.PathLike[str] | None = None,
    tk_data_dir: str | os.PathLike[str] | None = None,
    distributions: Iterable[metadata.Distribution] | None = None,
    interpreter_license: str | os.PathLike[str] | None = None,
    python_version: str | None = None,
    overrides_dir: str | os.PathLike[str] | None = LICENSE_OVERRIDES_DIR,
    frontend_dir: str | os.PathLike[str] = FRONTEND_DIR,
) -> tuple[str, list[Component]]:
    """Render the notices for an analysis; raises naming every missing text."""
    tables = [list(table) for table in tables]
    sources = source_paths(tables)
    problems: list[str] = []
    components: list[Component] = []
    try:
        components += base_components(
            include_tcl_tk=tkinter_bundled(sources),
            tcl_data_dir=tcl_data_dir,
            tk_data_dir=tk_data_dir,
            interpreter_license=interpreter_license,
            python_version=python_version,
        )
    except NoticesError as exc:
        problems += exc.problems
    try:
        dists = bundled_distributions(sources, distributions)
        components += distribution_components(dists, overrides_dir=overrides_dir)
    except NoticesError as exc:
        problems += exc.problems
    try:
        components += frontend_components(frontend_dir, overrides_dir=overrides_dir)
    except NoticesError as exc:
        problems += exc.problems
    if problems:
        raise NoticesError(problems)
    return render_notices(components), components


def write_third_party_notices(
    out_path: str | os.PathLike[str],
    tables: Iterable[Iterable[Sequence[object]]],
    **kwargs,
) -> pathlib.Path:
    """Build the notices (see :func:`build_notices`) and write them to ``out_path``.

    Written as UTF-8 with a byte-order mark and Windows line endings, so every
    Notepad version shows the non-ASCII characters in license texts correctly.
    Nothing is written when a text is missing: the error propagates and fails
    the build. A notices file from an earlier build at ``out_path`` is removed
    first, so a failed build cannot leave it behind for the installer to pick up.
    """
    out = pathlib.Path(out_path)
    out.unlink(missing_ok=True)
    document, components = build_notices(tables, **kwargs)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write(document)
    counts = {kind: len([c for c in components if c.kind == kind]) for kind, _ in _GROUPS}
    print(
        f"Third-party notices: {counts['runtime']} runtime, {counts['python']} Python "
        f"and {counts['npm']} npm components -> {out}"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    """Check the interpreter's and the frontend's license texts can be found.

    ``--frontend-only`` checks the npm packages alone (CI's frontend job,
    whose runner Python is not the one a release bundles).
    """
    args = list(sys.argv[1:] if argv is None else argv)
    frontend_only = "--frontend-only" in args
    unknown = [a for a in args if a != "--frontend-only"]
    if unknown:
        print(f"ERROR: unknown argument(s): {' '.join(unknown)}", file=sys.stderr)
        return 2
    checks = (frontend_components,) if frontend_only else (base_components, frontend_components)
    problems: list[str] = []
    components: list[Component] = []
    for build in checks:
        try:
            components += build()
        except NoticesError as exc:
            problems += exc.problems
    if problems:
        print(f"ERROR: {NoticesError(problems)}", file=sys.stderr)
        return 1
    for component in (c for c in components if c.kind == "runtime"):
        for text in component.texts:
            print(f"{component.title}: {text.source} ({len(text.text.splitlines())} lines)")
    npm = len([c for c in components if c.kind == "npm"])
    if frontend_only:
        print(f"{npm} npm package license texts OK.")
    else:
        print(
            f"Interpreter license and {npm} npm package license texts OK; the "
            "bundled Python packages' texts are checked by the spec after the "
            "analysis."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
