"""The Windows build's third-party notices (packaging/windows/third_party_notices.py).

Copied from Claude-Spec-Critic ``tests/test_packaging_entry.py`` (its notices
classes) and adapted: Tcl/Tk only when the analysis bundles ``_tkinter``, the
required WebView2 SDK supplement, the frontend's npm packages, and Build-a-Spec
identity strings. Hermetic: fake distributions and a fake node_modules tree
under ``tmp_path``; the two checks against the real tree skip when it is not
installed.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG = REPO_ROOT / "packaging" / "windows"


def _load(name: str):
    script = PKG / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"packaging_windows_{name}", script)
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs, as a real import is: dataclasses resolves a
    # class's string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture
def notices():
    return _load("third_party_notices")


def _fake_dist(
    site: Path,
    name: str,
    version: str,
    *,
    files: dict[str, str | bytes] | None = None,
    meta_files: dict[str, str | bytes] | None = None,
    meta_lines: tuple[str, ...] = (),
) -> dict[str, Path]:
    """Install a minimal distribution into ``site`` (used as a sys.path entry).

    ``files`` are paths relative to ``site`` (package code, vendored notices);
    ``meta_files`` are relative to the ``.dist-info`` folder. Every file is
    listed in ``RECORD``, as a wheel install lists it. Returns the absolute
    path of each written file, keyed as given.
    """
    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    written: dict[str, str | bytes] = {
        f"{dist_info}/METADATA": "\n".join(
            ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}", *meta_lines]
        )
        + "\n"
    }
    keys = {}
    for rel, content in (meta_files or {}).items():
        written[f"{dist_info}/{rel}"] = content
        keys[rel] = f"{dist_info}/{rel}"
    for rel, content in (files or {}).items():
        written[rel] = content
        keys[rel] = rel
    for rel, content in written.items():
        path = site / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    rows = [f"{rel},," for rel in written] + [f"{dist_info}/RECORD,,"]
    (site / dist_info / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return {key: site / rel for key, rel in keys.items()}


def _dists(site: Path) -> list:
    return list(importlib.metadata.distributions(path=[str(site)]))


def _dist(site: Path, name: str):
    [dist] = [d for d in _dists(site) if d.metadata["Name"] == name]
    return dist


def _mit(holder: str) -> str:
    return f"MIT License\n\nCopyright (c) {holder}\n\nPermission is hereby granted, free of charge.\n"


# Tcl 8.6.12's and Tk 8.6.12's license.terms (the copies CPython's Windows
# build uses), abridged between the opening and closing sentences the helper
# anchors on, with the CRLF endings and line wrapping those files have.
_TCL_TERMS = (
    "This software is copyrighted by the Regents of the University of\r\n"
    "California, Sun Microsystems, Inc., Scriptics Corporation, ActiveState\r\n"
    "Corporation and other parties.  The following terms apply to all files\r\n"
    "associated with the software unless explicitly disclaimed in\r\n"
    "individual files.\r\n"
    "\r\n"
    "GOVERNMENT USE: ... 252.227-7014 (b) (3) of DFARs.  Notwithstanding the\r\n"
    "foregoing, the authors grant the U.S. Government and others acting in its\r\n"
    "behalf permission to use and distribute the software in accordance with the\r\n"
    "terms specified in this license.\r\n"
)
_TK_TERMS = (
    "This software is copyrighted by the Regents of the University of\r\n"
    "California, Sun Microsystems, Inc., Scriptics Corporation, ActiveState\r\n"
    "Corporation, Apple Inc. and other parties.  The following terms apply to\r\n"
    "all files associated with the software unless explicitly disclaimed in\r\n"
    "individual files.\r\n"
    "\r\n"
    "GOVERNMENT USE: ... 252.227-7013 (b) (3) of DFARs.  Notwithstanding the\r\n"
    "foregoing, the authors grant the U.S. Government and others acting in its\r\n"
    "behalf permission to use and distribute the software in accordance with the\r\n"
    "terms specified in this license.\r\n"
)
_PSF = (
    "A. HISTORY OF THE SOFTWARE\r\n"
    "==========================\r\n"
    "\r\n"
    "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2\r\n"
    "--------------------------------------------\r\n"
    "\r\n"
    "1. This LICENSE AGREEMENT is between the Python Software Foundation.\r\n"
)


def _python_org_install(root: Path) -> dict[str, Path]:
    """The layout of python.org's Windows install (CPython 3.12).

    ``LICENSE.txt`` is the PSF license with the bundled libraries' terms
    appended — Tcl's, then Tk's (``PCbuild/regen.targets``) — and
    ``tcl\\tk8.6`` carries ``license.terms`` while ``tcl\\tcl8.6`` has none.
    """
    base = root / "Python312"
    tcl = base / "tcl" / "tcl8.6"
    tk = base / "tcl" / "tk8.6"
    tcl.mkdir(parents=True)
    tk.mkdir(parents=True)
    (tcl / "init.tcl").write_text("# init\n", encoding="utf-8")
    (tk / "license.terms").write_bytes(_TK_TERMS.encode())
    license_txt = base / "LICENSE.txt"
    license_txt.write_bytes(
        (_PSF + "\r\nThis program is linked with and uses Microsoft Distributable Code.\r\n"
         + "\r\n" + _TCL_TERMS + "\r\n" + _TK_TERMS).encode()
    )
    return {"base": base, "license": license_txt, "tcl": tcl, "tk": tk}


def _frontend(root: Path, packages: dict[str, dict], files: dict[str, str | None]) -> Path:
    """A frontend folder: ``package-lock.json`` with ``packages`` (keys are
    lockfile paths) and, for each key in ``files``, an installed package
    directory holding that license file name (None: installed, no license)."""
    root.mkdir(parents=True, exist_ok=True)
    table = {"": {"name": "build-a-spec-frontend", "version": "1.0.0"}, **packages}
    (root / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": table}), encoding="utf-8"
    )
    for key, license_name in files.items():
        package_dir = root / key
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "package.json").write_text("{}", encoding="utf-8")
        (package_dir / "index.js").write_text("export {}\n", encoding="utf-8")
        if license_name:
            (package_dir / license_name).write_text(_mit(key.rsplit("/", 1)[-1]), encoding="utf-8")
    return root


def _empty_frontend(root: Path) -> Path:
    return _frontend(root, {}, {})


def _notices_kwargs(install: dict[str, Path], site: Path, frontend: Path) -> dict:
    return {
        "tcl_data_dir": install["tcl"],
        "tk_data_dir": install["tk"],
        "interpreter_license": install["license"],
        "python_version": "3.12.10",
        "distributions": _dists(site),
        "overrides_dir": None,
        "frontend_dir": frontend,
    }


class TestBundledDistributions:
    """A distribution is bundled when the analysis collected a file it owns."""

    def test_owner_of_a_collected_file_is_listed_and_others_are_not(self, tmp_path, notices):
        site = tmp_path / "site"
        shipped = _fake_dist(site, "shipped", "1.0", files={"shipped/__init__.py": ""})
        _fake_dist(site, "installed-only", "2.0", files={"installed_only/__init__.py": ""})
        _fake_dist(site, "pyinstaller", "6.22.3")

        dists = notices.bundled_distributions(
            [str(shipped["shipped/__init__.py"])], _dists(site)
        )
        # Build-time-only and test-only packages (pytest, ruff, ...) are
        # installed but never bundled; they must not be listed. PyInstaller
        # always is: its bootloader is BuildASpec.exe.
        assert [d.metadata["Name"] for d in dists] == ["pyinstaller", "shipped"]

    def test_pyinstaller_is_always_listed_and_must_be_installed(self, tmp_path, notices):
        site = tmp_path / "site"
        _fake_dist(site, "shipped", "1.0", files={"shipped/__init__.py": ""})
        with pytest.raises(notices.NoticesError, match="pyinstaller is not installed"):
            notices.bundled_distributions([], _dists(site))

    def test_build_a_spec_itself_is_never_listed(self, tmp_path, notices):
        site = tmp_path / "site"
        own = _fake_dist(site, "build-a-spec", "1.28.1", files={"backend/__init__.py": ""})
        _fake_dist(site, "pyinstaller", "6.22.3")
        dists = notices.bundled_distributions([str(own["backend/__init__.py"])], _dists(site))
        assert [d.metadata["Name"] for d in dists] == ["pyinstaller"]

    def test_paths_are_normalized_and_the_first_copy_of_a_name_wins(self, tmp_path, notices):
        first, second = tmp_path / "first", tmp_path / "second"
        _fake_dist(first, "Shared_Name", "1.0", files={"shared/a.py": ""})
        _fake_dist(second, "shared-name", "9.9", files={"shared/b.py": ""})
        _fake_dist(first, "pyinstaller", "6.22.3")
        roundabout = first / "shared" / ".." / "shared" / "a.py"
        dists = notices.bundled_distributions(
            [str(roundabout), str(second / "shared" / "b.py")], _dists(first) + _dists(second)
        )
        assert [(d.metadata["Name"], d.version) for d in dists] == [
            ("pyinstaller", "6.22.3"),
            ("Shared_Name", "1.0"),
        ]

    def test_source_paths_reads_every_table_and_skips_entries_without_one(self, notices):
        pure = [("mod", "/x/mod.py", "PYMODULE"), ("ns", None, "PYMODULE")]
        binaries = [("a.pyd", "/x/a.pyd", "EXTENSION"), ("short",)]
        datas = [("d/f", "", "DATA"), ("d/g", "/x/g", "DATA")]
        assert notices.source_paths([pure, binaries, datas]) == ["/x/mod.py", "/x/a.pyd", "/x/g"]

    def test_tkinter_is_detected_only_by_its_extension_module(self, notices):
        assert notices.tkinter_bundled(["C:/Py/DLLs/_tkinter.pyd"])
        assert notices.tkinter_bundled(["/usr/lib/python3.12/lib-dynload/_tkinter.cpython-312.so"])
        assert not notices.tkinter_bundled(["/x/tkinter_helpers.py", "/x/_tkinterish.py"])
        assert not notices.tkinter_bundled([])


class TestDistributionLicenseTexts:
    def test_declared_license_files_are_read_in_both_layouts(self, tmp_path, notices):
        site = tmp_path / "site"
        # Metadata 2.4 keeps declared files under .dist-info/licenses/ ...
        _fake_dist(site, "modern", "1.0", meta_files={"licenses/LICENSE": _mit("Modern")},
                   meta_lines=("License-File: LICENSE",))
        # ... older setuptools wheels directly in .dist-info.
        _fake_dist(site, "older", "1.0", meta_files={"COPYING.txt": _mit("Older")},
                   meta_lines=("License-File: COPYING.txt",))
        modern = notices.distribution_license_texts(_dist(site, "modern"), overrides_dir=None)
        older = notices.distribution_license_texts(_dist(site, "older"), overrides_dir=None)
        assert [(t.source, t.text) for t in modern] == [
            ("modern-1.0.dist-info/licenses/LICENSE", _mit("Modern").rstrip())
        ]
        assert [t.source for t in older] == ["older-1.0.dist-info/COPYING.txt"]

    def test_undeclared_files_then_vendored_notices_follow_the_declared_ones(self, tmp_path, notices):
        site = tmp_path / "site"
        _fake_dist(
            site, "pkg", "1.0",
            meta_lines=("License-File: LICENSE.MIT",),
            meta_files={
                "licenses/LICENSE.MIT": _mit("Pkg"),
                # Present but not declared.
                "licenses/NOTICE": "Pkg notice\n",
            },
            files={
                "pkg/__init__.py": "",
                # Third-party code vendored inside the package.
                "pkg/_vendor/lib/LICENSE": "Apache License 2.0 for lib\n",
            },
        )
        texts = notices.distribution_license_texts(_dist(site, "pkg"), overrides_dir=None)
        assert [t.source for t in texts] == [
            "pkg-1.0.dist-info/licenses/LICENSE.MIT",
            "pkg-1.0.dist-info/licenses/NOTICE",
            "pkg/_vendor/lib/LICENSE",
        ]

    def test_a_module_named_license_is_code_not_a_text(self, tmp_path, notices):
        site = tmp_path / "site"
        _fake_dist(site, "pkg", "1.0", files={"pkg/license.py": "X = 1\n", "pkg/licenses.pyc": b"\0"})
        assert notices.distribution_license_texts(_dist(site, "pkg"), overrides_dir=None) == ()

    def test_blank_files_do_not_count_and_non_utf8_text_is_kept(self, tmp_path, notices):
        site = tmp_path / "site"
        _fake_dist(site, "blank", "1.0", meta_files={"LICENSE": "  \r\n\n"})
        _fake_dist(site, "latin", "1.0", meta_files={"LICENSE": "Copyright \xa9 J\xfcrgen\r\n".encode("latin-1")})
        assert notices.distribution_license_texts(_dist(site, "blank"), overrides_dir=None) == ()
        [text] = notices.distribution_license_texts(_dist(site, "latin"), overrides_dir=None)
        assert text.text == "Copyright \xa9 J\xfcrgen"

    def test_full_text_license_field_counts_only_when_no_file_does(self, tmp_path, notices):
        site = tmp_path / "site"
        field = ("License: MIT License", "        ", "        Copyright (c) 2022 Someone",
                 "        Permission is hereby granted, free of charge.")
        _fake_dist(site, "field-only", "1.0", meta_lines=field)
        _fake_dist(site, "short-field", "1.0", meta_lines=("License: MIT",))
        _fake_dist(site, "both", "1.0", meta_lines=field, meta_files={"LICENSE": _mit("Both")})

        [text] = notices.distribution_license_texts(_dist(site, "field-only"), overrides_dir=None)
        assert text.source == "the License field of its package metadata"
        assert "Copyright (c) 2022 Someone" in text.text
        assert notices.distribution_license_texts(_dist(site, "short-field"), overrides_dir=None) == ()
        [text] = notices.distribution_license_texts(_dist(site, "both"), overrides_dir=None)
        assert text.source == "both-1.0.dist-info/LICENSE"

    def test_override_file_is_used_only_for_its_exact_version(self, tmp_path, notices):
        site, overrides = tmp_path / "site", tmp_path / "overrides"
        _fake_dist(site, "No_License", "1.2.3")
        overrides.mkdir()
        (overrides / "no-license-1.2.3.txt").write_text(_mit("Upstream"), encoding="utf-8")
        [text] = notices.distribution_license_texts(_dist(site, "No_License"), overrides_dir=overrides)
        assert "third_party_licenses/no-license-1.2.3.txt" in text.source
        assert "supplied with Build-a-Spec" in text.source
        assert "Copyright (c) Upstream" in text.text

        # An upgrade needs a text checked against the new version.
        (overrides / "no-license-1.2.3.txt").rename(overrides / "no-license-1.2.2.txt")
        assert notices.distribution_license_texts(_dist(site, "No_License"), overrides_dir=overrides) == ()

    def test_license_identifier_precedence(self, tmp_path, notices):
        site = tmp_path / "site"
        _fake_dist(site, "expr", "1", meta_lines=("License-Expression: MIT OR Apache-2.0", "License: BSD"))
        _fake_dist(site, "field", "1", meta_lines=("License: MPL-2.0",))
        _fake_dist(site, "classifier", "1", meta_lines=(
            "License: UNKNOWN",
            "Classifier: License :: OSI Approved :: BSD License",
            "Classifier: Programming Language :: Python",
        ))
        # pywebview 6.2.1 puts its whole BSD text in License and has no
        # License-Expression: its first line names it.
        _fake_dist(site, "fulltext", "1", meta_lines=(
            "License: BSD 3-Clause License", "        ", "        Copyright (c) 2014",
        ))
        _fake_dist(site, "nothing", "1")
        names = ("expr", "field", "classifier", "fulltext", "nothing")
        ident = {n: notices.license_identifier(_dist(site, n)) for n in names}
        assert ident == {
            "expr": "MIT OR Apache-2.0",
            "field": "MPL-2.0",
            "classifier": "BSD License",
            "fulltext": "BSD 3-Clause License",
            "nothing": "not declared in its package metadata (see the text below)",
        }


class TestRequiredSupplements:
    """pywebview's wheel ships Microsoft's WebView2 SDK DLLs without their
    license; the supplement is required whenever pywebview is bundled."""

    def _pywebview(self, site: Path, version: str = "6.2.1") -> None:
        _fake_dist(site, "pywebview", version, meta_files={"licenses/LICENSE": _mit("pywebview")},
                   meta_lines=("License-File: LICENSE",),
                   files={"webview/lib/Microsoft.Web.WebView2.Core.dll": b"MZ"})

    def test_the_supplement_is_appended_after_the_packages_own_text(self, tmp_path, notices):
        site, overrides = tmp_path / "site", tmp_path / "overrides"
        self._pywebview(site)
        overrides.mkdir()
        (overrides / "pywebview-6.2.1-webview2-sdk.txt").write_text(
            "Copyright (C) Microsoft Corporation.\n", encoding="utf-8"
        )
        [component] = notices.distribution_components(
            [_dist(site, "pywebview")], overrides_dir=overrides
        )
        assert [t.source.split(" ")[0] for t in component.texts] == [
            "pywebview-6.2.1.dist-info/licenses/LICENSE",
            "packaging/windows/third_party_licenses/pywebview-6.2.1-webview2-sdk.txt",
        ]
        assert "Microsoft Edge WebView2 SDK" in component.texts[1].source
        assert component.texts[1].text == "Copyright (C) Microsoft Corporation."

    def test_a_missing_supplement_fails_the_build_and_names_the_file(self, tmp_path, notices):
        site, overrides = tmp_path / "site", tmp_path / "overrides"
        self._pywebview(site, "6.3.0")
        overrides.mkdir()
        # Only the previous version's text exists: an upgrade must be re-checked.
        (overrides / "pywebview-6.2.1-webview2-sdk.txt").write_text("old\n", encoding="utf-8")
        with pytest.raises(notices.NoticesError) as excinfo:
            notices.distribution_components([_dist(site, "pywebview")], overrides_dir=overrides)
        [problem] = excinfo.value.problems
        assert "pywebview 6.3.0 vendors third-party binaries" in problem
        assert "pywebview-6.3.0-webview2-sdk.txt" in problem
        assert "Microsoft.Web.WebView2.LICENSE.md" in problem  # where to get it

    def test_the_checked_in_supplement_is_microsofts_text(self, notices):
        [path] = sorted(notices.LICENSE_OVERRIDES_DIR.glob("pywebview-*-webview2-sdk.txt"))
        text = path.read_text(encoding="utf-8")
        assert "Copyright (C) Microsoft Corporation. All rights reserved." in text
        for dll in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll",
                    "WebView2Loader.dll"):
            assert dll in text


class TestFrontendComponents:
    def test_production_packages_are_listed_with_their_license_files(self, tmp_path, notices):
        frontend = _frontend(
            tmp_path / "frontend",
            {
                "node_modules/react": {"version": "18.3.1", "license": "MIT"},
                "node_modules/@dnd-kit/core": {"version": "6.3.1", "license": "MIT"},
                "node_modules/mermaid/node_modules/uuid": {"version": "9.0.0", "license": "MIT"},
                "node_modules/vite": {"version": "6.0.0", "dev": True, "license": "MIT"},
                "node_modules/fsevents": {"version": "2.3.3", "devOptional": True},
                "node_modules/khroma": {"version": "2.1.0"},
            },
            {
                "node_modules/react": "LICENSE",
                "node_modules/@dnd-kit/core": "LICENSE.md",
                "node_modules/mermaid/node_modules/uuid": "LICENSE.txt",
                "node_modules/khroma": "license",
                # vite is installed but dev: never listed.
                "node_modules/vite": "LICENSE.md",
            },
        )
        (frontend / "node_modules" / "khroma" / "package.json").write_text(
            json.dumps({"license": "MIT"}), encoding="utf-8"
        )
        components = notices.frontend_components(frontend, overrides_dir=None)
        assert [(c.title, c.license, c.kind) for c in components] == [
            ("@dnd-kit/core 6.3.1", "MIT", "npm"),
            # The lockfile carries no license field; package.json supplies it.
            ("khroma 2.1.0", "MIT", "npm"),
            ("react 18.3.1", "MIT", "npm"),
            ("uuid 9.0.0", "MIT", "npm"),
        ]
        assert components[0].texts[0].source == "node_modules/@dnd-kit/core/LICENSE.md"

    def test_an_absent_optional_package_is_skipped_but_a_required_one_fails(self, tmp_path, notices):
        frontend = _frontend(
            tmp_path / "frontend",
            {
                "node_modules/@types/trusted-types": {"version": "2.0.7", "optional": True},
                "node_modules/react": {"version": "18.3.1"},
            },
            {},
        )
        with pytest.raises(notices.NoticesError) as excinfo:
            notices.frontend_components(frontend, overrides_dir=None)
        [problem] = excinfo.value.problems
        assert problem.startswith("npm package react 18.3.1 is a production dependency but is not installed")
        assert "npm ci" in problem

    def test_a_package_without_a_license_file_fails_unless_overridden(self, tmp_path, notices):
        frontend = _frontend(
            tmp_path / "frontend",
            {"node_modules/@scope/bare": {"version": "1.0.0", "license": "ISC"}},
            {"node_modules/@scope/bare": None},
        )
        with pytest.raises(notices.NoticesError) as excinfo:
            notices.frontend_components(frontend, overrides_dir=None)
        assert "ships no license file" in excinfo.value.problems[0]

        overrides = tmp_path / "overrides"
        overrides.mkdir()
        # A scope's slash cannot be in a file name; it is written as "+".
        (overrides / "npm-@scope+bare-1.0.0.txt").write_text("ISC text\n", encoding="utf-8")
        [component] = notices.frontend_components(frontend, overrides_dir=overrides)
        assert component.texts[0].text == "ISC text"
        assert "npm-@scope+bare-1.0.0.txt" in component.texts[0].source

    def test_an_unreadable_lockfile_fails(self, tmp_path, notices):
        with pytest.raises(notices.NoticesError, match="lockfile .* cannot be read"):
            notices.frontend_components(tmp_path / "nowhere", overrides_dir=None)

    def test_every_production_package_in_the_real_tree_ships_a_license(self, notices):
        if not (notices.FRONTEND_DIR / "node_modules").is_dir():
            pytest.skip("frontend/node_modules is not installed (CI's frontend job checks it)")
        components = notices.frontend_components()
        assert len(components) > 50
        assert {"react", "react-dom", "mermaid", "dompurify"} <= {c.name for c in components}


class TestMissingLicenseFailsTheBuild:
    """The build stops, naming every gap, and leaves no notices file behind."""

    def test_every_gap_is_named_and_nothing_is_written(self, tmp_path, notices):
        install = _python_org_install(tmp_path)
        site = tmp_path / "site"
        good = _fake_dist(site, "good", "1.0", files={"good/__init__.py": ""},
                          meta_files={"LICENSE": _mit("Good")})
        bad1 = _fake_dist(site, "bad-one", "0.1", files={"bad_one/__init__.py": ""})
        bad2 = _fake_dist(site, "bad-two", "0.2", files={"bad_two/core.pyd": b"\0"})
        _fake_dist(site, "pyinstaller", "6.22.3", meta_files={"COPYING.txt": "GPL with exception\n"})
        frontend = _frontend(
            tmp_path / "frontend",
            {"node_modules/bare": {"version": "3.0.0"}},
            {"node_modules/bare": None},
        )
        tables = [
            [("good", str(good["good/__init__.py"]), "PYMODULE"),
             ("bad_one", str(bad1["bad_one/__init__.py"]), "PYMODULE")],
            [("bad_two/core.pyd", str(bad2["bad_two/core.pyd"]), "EXTENSION")],
        ]
        out = tmp_path / "dist" / notices.NOTICES_FILENAME
        out.parent.mkdir()
        out.write_text("notices from an earlier build", encoding="utf-8")

        with pytest.raises(notices.NoticesError) as excinfo:
            notices.write_third_party_notices(out, tables, **_notices_kwargs(install, site, frontend))

        message = str(excinfo.value)
        assert "bad-one 0.1 is bundled but ships no license text" in message
        assert "bad-two 0.2 is bundled but ships no license text" in message
        assert "npm package bare 3.0.0 is a production dependency but ships no license file" in message
        assert "good" not in message
        assert "<name>-<version>.txt" in message  # the remedy
        assert len(excinfo.value.problems) == 3
        # A stale file would let the installer ship the previous build's list.
        assert not out.exists()

    def test_tcl_tk_gaps_count_only_when_tkinter_is_bundled(self, tmp_path, notices):
        site = tmp_path / "site"
        _fake_dist(site, "pyinstaller", "6.22.3", meta_files={"COPYING.txt": "GPL\n"})
        empty = tmp_path / "empty"
        (empty / "tcl8.6").mkdir(parents=True)
        (empty / "tk8.6").mkdir()
        install = _python_org_install(tmp_path)
        install["license"].write_bytes(_PSF.encode())  # no Tcl/Tk blocks to fall back on
        kwargs = {
            "tcl_data_dir": empty / "tcl8.6",
            "tk_data_dir": empty / "tk8.6",
            "interpreter_license": install["license"],
            "distributions": _dists(site),
            "overrides_dir": None,
            "frontend_dir": _empty_frontend(tmp_path / "frontend"),
        }
        # Build-a-Spec does not bundle tkinter, so the missing terms are moot.
        notices.build_notices([[]], **kwargs)

        tk_ext = [("_tkinter", str(tmp_path / "_tkinter.pyd"), "EXTENSION")]
        with pytest.raises(notices.NoticesError) as excinfo:
            notices.build_notices([tk_ext], **kwargs)
        problems = excinfo.value.problems
        assert any(p.startswith("the Tcl license terms were found neither") for p in problems)
        assert any(p.startswith("the Tk license terms were found neither") for p in problems)


class TestInterpreterAndTclTk:
    def test_python_org_windows_layout(self, tmp_path, notices):
        install = _python_org_install(tmp_path)
        [python] = notices.base_components(
            interpreter_license=install["license"], python_version="3.12.10"
        )
        assert (python.title, python.license, python.kind) == ("Python 3.12.10", "PSF-2.0", "runtime")
        # The interpreter's license is reproduced whole: on python.org's
        # Windows build it also carries the terms of the libraries it ships.
        assert "Microsoft Distributable Code" in python.texts[0].text
        assert "\r" not in python.texts[0].text

    def test_tcl_tk_when_tkinter_is_bundled(self, tmp_path, notices):
        install = _python_org_install(tmp_path)
        _, tcl, tk = notices.base_components(
            include_tcl_tk=True,
            tcl_data_dir=install["tcl"],
            tk_data_dir=install["tk"],
            interpreter_license=install["license"],
        )
        # tcl8.6 has no license file of its own; its terms come out of the
        # interpreter's LICENSE.txt — Tcl's block, not Tk's.
        assert (tcl.title, tcl.license) == ("Tcl 8.6", "TCL (the Tcl/Tk license)")
        [tcl_text] = tcl.texts
        assert "interpreter's LICENSE.txt" in tcl_text.source
        assert "ActiveState\nCorporation and other parties." in tcl_text.text
        assert "Apple Inc." not in tcl_text.text
        assert tcl_text.text.endswith("terms specified in this license.")
        [tk_text] = tk.texts
        assert tk_text.source == "license.terms in the bundled tk8.6 library directory"

    def test_an_unknown_tcl_tk_directory_fails_only_when_bundled(self, tmp_path, notices):
        install = _python_org_install(tmp_path)
        assert len(notices.base_components(interpreter_license=install["license"])) == 1
        with pytest.raises(notices.NoticesError) as excinfo:
            notices.base_components(include_tcl_tk=True, interpreter_license=install["license"])
        assert [p.split(" library directory")[0] for p in excinfo.value.problems] == [
            "tkinter is bundled but the Tcl", "tkinter is bundled but the Tk",
        ]

    def test_a_license_that_is_not_pythons_is_refused(self, tmp_path, notices):
        install = _python_org_install(tmp_path)
        install["license"].write_text("MIT License\n\nSomething else entirely.\n", encoding="utf-8")
        with pytest.raises(notices.NoticesError) as excinfo:
            notices.base_components(interpreter_license=install["license"])
        assert any("is not the Python license" in p for p in excinfo.value.problems)

    def test_interpreter_license_is_looked_up_in_the_base_install(self, tmp_path, notices):
        base, stdlib = tmp_path / "base", tmp_path / "lib" / "python3.12"
        base.mkdir()
        stdlib.mkdir(parents=True)
        (stdlib / "LICENSE.txt").write_text(_PSF, encoding="utf-8")
        # POSIX builds keep it with the standard library ...
        assert notices.interpreter_license_path(base, stdlib) == stdlib / "LICENSE.txt"
        # ... python.org's Windows install in its root, which wins.
        (base / "LICENSE.txt").write_text(_PSF, encoding="utf-8")
        assert notices.interpreter_license_path(base, stdlib) == base / "LICENSE.txt"
        with pytest.raises(notices.NoticesError, match="interpreter's license file was not found"):
            notices.interpreter_license_path(tmp_path / "none", tmp_path / "none")

    def test_cli_main_checks_the_environment_before_the_build(self, tmp_path, notices, monkeypatch, capsys):
        install = _python_org_install(tmp_path)
        frontend = _frontend(
            tmp_path / "frontend",
            {"node_modules/react": {"version": "18.3.1", "license": "MIT"}},
            {"node_modules/react": "LICENSE"},
        )
        monkeypatch.setattr(notices.sys, "base_prefix", str(install["base"]))
        # main() calls the module functions with their defaults; point the
        # frontend check at the fake tree.
        real = notices.frontend_components
        monkeypatch.setattr(notices, "frontend_components",
                            lambda *a, **k: real(frontend, overrides_dir=None))

        assert notices.main([]) == 0
        out = capsys.readouterr().out
        assert "Python " in out and "LICENSE.txt from the interpreter's install directory" in out
        assert "1 npm package license texts OK" in out

        assert notices.main(["--frontend-only"]) == 0
        out = capsys.readouterr().out
        assert out.strip() == "1 npm package license texts OK."

        install["license"].write_text("not python\n", encoding="utf-8")
        assert notices.main([]) == 1
        err = capsys.readouterr().err
        assert err.startswith("ERROR:")
        assert "is not the Python license" in err
        # The frontend-only check never looks at the interpreter.
        assert notices.main(["--frontend-only"]) == 0
        assert notices.main(["--nope"]) == 2


class TestNoticesDocument:
    def _build(self, tmp_path, notices):
        install = _python_org_install(tmp_path)
        site = tmp_path / "site"
        certifi = _fake_dist(site, "certifi", "2026.10.19", files={"certifi/cacert.pem": "pem"},
                             meta_lines=("License: MPL-2.0", "License-File: LICENSE"),
                             meta_files={"licenses/LICENSE": "Mozilla Public License Version 2.0\n\u00a9 text\n"})
        anyio = _fake_dist(site, "anyio", "4.15.1", files={"anyio/__init__.py": ""},
                           meta_lines=("License-Expression: MIT",),
                           meta_files={"licenses/LICENSE": _mit("Alex Gronholm")})
        _fake_dist(site, "pyinstaller", "6.22.3", meta_files={"COPYING.txt": "GPLv2 with bootloader exception\n"},
                   meta_lines=("License: GPLv2-or-later with a special exception",))
        _fake_dist(site, "altgraph", "0.17.5", files={"altgraph/__init__.py": ""},
                   meta_files={"LICENSE": _mit("altgraph")})
        frontend = _frontend(
            tmp_path / "frontend",
            {"node_modules/react": {"version": "18.3.1", "license": "MIT"}},
            {"node_modules/react": "LICENSE"},
        )
        tables = [
            [("anyio", str(anyio["anyio/__init__.py"]), "PYMODULE")],
            [],
            [("certifi/cacert.pem", str(certifi["certifi/cacert.pem"]), "DATA")],
        ]
        return tables, _notices_kwargs(install, site, frontend)

    def test_written_file_lists_and_reproduces_every_component(self, tmp_path, notices):
        tables, kwargs = self._build(tmp_path, notices)
        out = notices.write_third_party_notices(tmp_path / "dist" / notices.NOTICES_FILENAME, tables, **kwargs)
        data = out.read_bytes()
        # UTF-8 with a BOM and CRLF, so every Notepad version shows it right.
        assert data.startswith(b"\xef\xbb\xbf")
        assert b"\n" not in data.replace(b"\r\n", b"")
        text = data.decode("utf-8-sig").replace("\r\n", "\n")

        contents = text[text.index("Runtime\n"):text.index("=" * 78)]
        assert contents.splitlines() == [
            "Runtime",
            "-------",
            "  Python 3.12.10 - PSF-2.0",
            "",
            "Python packages",
            "---------------",
            "  anyio 4.15.1 - MIT",
            "  certifi 2026.10.19 - MPL-2.0",
            "  pyinstaller 6.22.3 - GPLv2-or-later with a special exception",
            "",
            "JavaScript packages (the user interface)",
            "----------------------------------------",
            "  react 18.3.1 - MIT",
            "",
            "",
        ]
        # Installed for the build but not bundled; and no tkinter, no Tcl/Tk.
        assert "altgraph" not in text
        assert "Tcl" not in text
        assert "Build-a-Spec's own license is in LICENSE.txt" in text
        assert "WebView2 bootstrapper" in text
        for heading, body in (
            ("anyio 4.15.1\nLicense: MIT", "Copyright (c) Alex Gronholm"),
            ("certifi 2026.10.19\nLicense: MPL-2.0", "Mozilla Public License Version 2.0\n\u00a9 text"),
            ("pyinstaller 6.22.3\nLicense: GPLv2", "GPLv2 with bootloader exception"),
            ("Python 3.12.10 (the bundled interpreter)\nLicense: PSF-2.0",
             "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2"),
            ("react 18.3.1 [npm]\nLicense: MIT", "--- node_modules/react/LICENSE ---"),
        ):
            assert heading in text
            assert body in text[text.index(heading):]

    def test_output_is_deterministic(self, tmp_path, notices):
        tables, kwargs = self._build(tmp_path, notices)
        first, _ = notices.build_notices(tables, **kwargs)
        reordered = [list(reversed(t)) for t in reversed(tables)]
        kwargs["distributions"] = list(reversed(kwargs["distributions"]))
        second, _ = notices.build_notices(reordered, **kwargs)
        assert first == second


def _lock_pins() -> dict[str, str]:
    """``{canonical name: version}`` for every ``==`` pin in requirements.txt."""
    pins = {}
    for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        match = re.fullmatch(r"([A-Za-z0-9._-]+)==(\S+)", line)
        if match:
            pins[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return pins


class TestTheLockShipsLicenseTexts:
    """The Windows build fails when a bundled package ships no license text;
    these find it on the pull request that bumps the pin, in the ordinary
    test run, not at release time."""

    def test_every_installed_pin_has_a_license_text(self, notices):
        pins = _lock_pins()
        assert len(pins) > 30
        checked, missing = [], []
        for name, version in pins.items():
            try:
                dist = importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:
                continue  # another platform's pin, or a local run without the lock
            if dist.version != version:
                continue  # a local venv that drifted from the lock
            checked.append(name)
            if not notices.distribution_license_texts(dist, overrides_dir=notices.LICENSE_OVERRIDES_DIR):
                missing.append(f"{name} {dist.version}")
        if not checked:
            pytest.skip("the lock is not installed in this environment")
        assert missing == [], f"packages that ship no license text: {missing}"

    def test_every_required_supplement_exists_for_the_pinned_version(self, notices):
        pins = _lock_pins()
        for name in notices.REQUIRED_SUPPLEMENTS:
            assert name in pins, f"{name} needs a supplement but is not in the lock"
            path = notices.supplement_path(name, pins[name])
            assert path is not None and path.is_file(), (
                f"requirements.txt pins {name} {pins[name]}, but {path} is missing; "
                "the Windows build would stop. Take the text from that version's source."
            )

    def test_every_override_names_a_pinned_version(self, notices):
        """A stale override (its package upgraded or dropped) is dead weight
        that reads as if it still covered something."""
        pins = _lock_pins()
        for path in sorted(notices.LICENSE_OVERRIDES_DIR.glob("*.txt")):
            if path.name.startswith("npm-"):
                continue
            stem = path.stem
            matched = [
                n for n, v in pins.items()
                if stem == f"{n}-{v}" or stem.startswith(f"{n}-{v}-")
            ]
            assert matched, f"{path.name} names no package version pinned in requirements.txt"
