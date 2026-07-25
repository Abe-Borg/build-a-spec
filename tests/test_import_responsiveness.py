"""The import/open paths must not freeze the rest of the app.

Parsing and lexically indexing a master specification is seconds of CPU on a
real section. It used to run inline in an ``async def`` handler, i.e. directly
on the asyncio event loop, so for the whole duration the server answered
nothing: the document panel sat still and a chat turn could not deliver a
single SSE frame until the upload finished. These tests pin the fix — the
blocking half runs on a worker thread, so concurrent requests keep flowing —
and the lexical-index memoization that made the blocking half far shorter.
"""
from __future__ import annotations

import io
import threading
import time

from docx import Document
from fastapi.testclient import TestClient

from backend import app as app_module

# How long a stalled request is allowed to hold the server before the test
# gives up on it. A blocked event loop cannot honour an asyncio timeout — the
# loop is the thing that is stuck — so every wait here is a real thread wait.
_BLOCK_SECONDS = 5.0
# A request served off the blocked path returns in milliseconds; anything near
# ``_BLOCK_SECONDS`` means it queued behind the import.
_RESPONSIVE_SECONDS = 1.5

_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _master_bytes(articles: int = 3, paragraphs: int = 3) -> bytes:
    document = Document()
    document.add_paragraph("SECTION 21 13 13")
    document.add_paragraph("WET-PIPE SPRINKLER SYSTEMS")
    for part_index, part in enumerate(
        ["PART 1 - GENERAL", "PART 2 - PRODUCTS", "PART 3 - EXECUTION"], 1
    ):
        document.add_paragraph(part)
        for article in range(articles):
            document.add_paragraph(
                f"{part_index}.{article + 1} ARTICLE {article + 1}"
            )
            for paragraph in range(paragraphs):
                document.add_paragraph(
                    f"{chr(65 + paragraph)}. Provision {article + 1}."
                    f"{paragraph + 1}: install per NFPA 13."
                )
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _upload(source: bytes) -> dict:
    return {"file": ("master.docx", source, _DOCX_MEDIA_TYPE)}


def test_import_does_not_block_concurrent_requests(monkeypatch):
    """A slow import must not stall an unrelated request on the event loop."""
    real_prepare = app_module._prepare_master_import
    entered = threading.Event()
    release = threading.Event()

    def blocking_prepare(source_bytes: bytes, safe_filename: str):
        entered.set()
        # Stands in for the seconds of real parsing/indexing CPU. Blocking a
        # thread is the point: on the event loop it freezes every other
        # request, exactly like the parse it replaces used to.
        release.wait(_BLOCK_SECONDS)
        return real_prepare(source_bytes, safe_filename)

    monkeypatch.setattr(app_module, "_prepare_master_import", blocking_prepare)
    source = _master_bytes()

    with TestClient(app_module.create_app()) as client:
        def do_import():
            return client.post("/api/import/master", files=_upload(source))

        worker_outcome: dict = {}

        def run_import() -> None:
            worker_outcome["response"] = do_import()

        worker = threading.Thread(target=run_import, daemon=True)
        worker.start()
        assert entered.wait(_BLOCK_SECONDS + 5), "import never started"
        started = time.perf_counter()
        health = client.get("/api/health")
        elapsed = time.perf_counter() - started
        release.set()
        worker.join(_BLOCK_SECONDS + 10)

    assert health.status_code == 200
    assert worker_outcome["response"].status_code == 200
    assert elapsed < _RESPONSIVE_SECONDS, (
        f"an unrelated request waited {elapsed:.2f}s for the import — the "
        "blocking work is back on the event loop"
    )


def test_project_load_does_not_block_concurrent_requests(monkeypatch):
    """The same guarantee for opening a project (it re-parses the master)."""
    real_stage = app_module._stage_project_load
    entered = threading.Event()
    release = threading.Event()

    def blocking_stage(payload: bytes):
        entered.set()
        release.wait(_BLOCK_SECONDS)
        return real_stage(payload)

    monkeypatch.setattr(app_module, "_stage_project_load", blocking_stage)

    with TestClient(app_module.create_app()) as client:
        saved = client.get("/api/project/save")
        assert saved.status_code == 200
        worker_outcome: dict = {}

        def run_load() -> None:
            worker_outcome["response"] = client.post(
                "/api/project/load-file",
                files={
                    "file": ("session.baspec", saved.content, "application/zip")
                },
            )

        worker = threading.Thread(target=run_load, daemon=True)
        worker.start()
        assert entered.wait(_BLOCK_SECONDS + 5), "load never started"
        started = time.perf_counter()
        health = client.get("/api/health")
        elapsed = time.perf_counter() - started
        release.set()
        worker.join(_BLOCK_SECONDS + 10)

    assert health.status_code == 200
    assert worker_outcome["response"].status_code == 200
    assert elapsed < _RESPONSIVE_SECONDS, (
        f"an unrelated request waited {elapsed:.2f}s for the project load — "
        "the blocking work is back on the event loop"
    )


def _document_xml(source: bytes) -> bytes:
    import zipfile

    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        return archive.read("word/document.xml")


def test_a_real_import_keeps_the_server_answering():
    """End-to-end: no monkeypatching, a genuine multi-hundred-block master.

    The offload has to cover the *whole* handler, not just the parse. Building
    the response payload runs the first source-capability sweep, which on an
    imported master is the most expensive thing the app does — left on the
    event loop it would freeze the server exactly like the parse used to.
    """
    # Sized so the response-building phase (the capability sweep) is several
    # seconds on its own — small enough to stay a reasonable test, large
    # enough that leaving any phase on the loop is unmissable.
    source = _master_bytes(articles=6, paragraphs=8)
    health_times: list[float] = []
    done = threading.Event()

    with TestClient(app_module.create_app()) as client:
        outcome: dict = {}

        def run_import() -> None:
            try:
                outcome["response"] = client.post(
                    "/api/import/master", files=_upload(source)
                )
            finally:
                done.set()

        worker = threading.Thread(target=run_import, daemon=True)
        worker.start()
        # Poll health for as long as the import runs. Every sample must come
        # back promptly; one slow sample means the loop was blocked.
        while not done.wait(0.05):
            started = time.perf_counter()
            assert client.get("/api/health").status_code == 200
            health_times.append(time.perf_counter() - started)
            if len(health_times) > 400:
                break
        worker.join(120)

    assert outcome["response"].status_code == 200
    assert health_times, "the import finished too fast to sample — raise the fixture size"
    assert max(health_times) < _RESPONSIVE_SECONDS, (
        f"slowest health check during a real import was {max(health_times):.2f}s "
        f"over {len(health_times)} samples — the server stalls while importing"
    )


def test_anchor_lookups_never_rescan_the_element_table():
    """Anchor resolution must be O(1), not a scan of every element.

    Binding a master's anchors calls these lookups once per body child. When
    each call walked the whole ``elements`` tuple the import was quadratic —
    ~12s of blocking CPU for a 6k-paragraph section. Counting iterations pins
    the complexity class directly, with no dependence on machine speed.
    """
    from backend.spec_doc.xml_lexical import build_source_xml_index

    index = build_source_xml_index(_document_xml(_master_bytes(articles=6)))
    snapshot = list(index.elements)
    assert len(snapshot) > 50, "fixture too small to be meaningful"

    scans = {"count": 0}

    class CountingTuple(tuple):
        def __iter__(self):
            scans["count"] += 1
            return tuple.__iter__(self)

    object.__setattr__(index, "elements", CountingTuple(snapshot))

    for element in snapshot:
        index.element_for_span(element.element_span)
        index.direct_children(element)

    # One pass to build each of the two element-keyed maps, and never again —
    # regardless of how many lookups follow.
    assert scans["count"] <= 2, (
        f"{scans['count']} full scans of the element table for "
        f"{len(snapshot) * 2} lookups — the memoized maps are not being used"
    )


def test_memoized_lookups_match_a_linear_scan():
    """The memoized anchor maps return exactly what the old scans returned."""
    import pytest

    from backend.spec_doc.xml_lexical import (
        XmlLexicalError,
        build_source_xml_index,
    )

    index = build_source_xml_index(
        _document_xml(_master_bytes(articles=4, paragraphs=3))
    )

    for child in index.body_children:
        assert index.body_child(child.body_child_index) is child
    for node in index.word_text_nodes:
        assert (
            index.word_text(node.body_child_index, node.text_node_ordinal)
            is node
        )
    for element in index.elements:
        assert index.element_for_span(element.element_span) is element
        expected = tuple(
            candidate
            for candidate in index.elements
            if candidate.parent_start == element.element_span.start
        )
        assert index.direct_children(element) == expected

    # Misses still raise the same typed blockers rather than returning None.
    with pytest.raises(XmlLexicalError):
        index.body_child(10_000)
    with pytest.raises(XmlLexicalError):
        index.word_text(10_000, 0)
