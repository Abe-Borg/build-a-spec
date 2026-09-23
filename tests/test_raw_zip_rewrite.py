"""Several members rebuilt and new ones appended, with raw-record fidelity.

Redline on your original, Phase 3: the comments on a redline live in parts
of their own (``word/comments.xml`` and its relationships) and are registered
in two more (``[Content_Types].xml``, ``word/_rels/document.xml.rels``), so
the raw ZIP writer has to replace several members and append new ones —
with the discipline the one-member writer always had. Asserted here with the
independent binary oracle of ``test_raw_zip_clone`` rather than only through
:mod:`zipfile`, which would miss recompression or metadata drift in the
members Build-a-Spec does not own.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from backend.spec_doc import raw_zip
from backend.spec_doc.raw_zip import (
    RawZipError,
    audit_raw_zip_rewrite,
    parse_raw_zip_archive,
    replace_raw_zip_member,
    rewrite_raw_zip_members,
)
from tests.test_raw_zip_clone import (
    _DOCUMENT_PART,
    _EntrySpec,
    _central_record,
    _local_record,
    _masked_central,
    _masked_local_header,
    _oracle,
    _rich_specs,
    _with_distinct_local_extra,
    _write_zip,
)

_COMMENTS = "word/comments.xml"
_COMMENTS_RELS = "word/_rels/comments.xml.rels"


def _source(*, descriptors: bool = False, envelope: bool = False) -> bytes:
    archive = _write_zip(
        _rich_specs(zipfile.ZIP_DEFLATED),
        archive_comment=b"rewrite archive comment\x00\xff",
        descriptors=descriptors,
    )
    if not descriptors:
        archive = _with_distinct_local_extra(archive)
    if envelope:
        return b"MZ\x90\x00PREAMBLE\x00" + archive + b"TRAILING\x00\xff"
    return archive


def _assert_rewrite(source: bytes, output: bytes, replacements, additions) -> None:
    """Every record the rewrite does not own is the source's, byte for byte;
    the replaced ones keep their shape; the appended ones come last."""
    before = _oracle(source)
    after = _oracle(output)
    added = [name for name, _payload in additions]
    assert [e.name for e in after.entries] == [e.name for e in before.entries] + added
    assert after.offset_base == before.offset_base
    assert after.comment == before.comment
    assert after.trailing == before.trailing
    first_before = min(e.local_start for e in before.entries)
    first_after = min(e.local_start for e in after.entries)
    assert source[:first_before] == output[:first_after]

    by_name = {e.name: e for e in after.entries}
    last_source_local = max(by_name[e.name].local_start for e in before.entries)
    for old in before.entries:
        new = by_name[old.name]
        if old.name in replacements:
            assert _masked_central(
                _central_record(source, old), mutable=True
            ) == _masked_central(_central_record(output, new), mutable=True)
            assert _masked_local_header(source, old) == _masked_local_header(
                output, new
            )
        else:
            assert _local_record(source, old) == _local_record(output, new)
            assert _masked_central(
                _central_record(source, old), mutable=False
            ) == _masked_central(_central_record(output, new), mutable=False)
    for name in added:  # after every source record, in order
        assert by_name[name].local_start > last_source_local
        assert by_name[name].method == zipfile.ZIP_DEFLATED
        assert by_name[name].flags == 0
    starts = [by_name[name].local_start for name in added]
    assert starts == sorted(starts)

    with zipfile.ZipFile(io.BytesIO(output), "r") as archive:
        assert archive.testzip() is None
        assert archive.namelist() == [e.name for e in before.entries] + added
        for name, payload in replacements.items():
            assert archive.read(name) == payload
        for name, payload in additions:
            assert archive.read(name) == payload


def test_several_members_are_replaced_and_new_ones_appended():
    source = _source()
    replacements = {
        _DOCUMENT_PART: b"<document><body>commented</body></document>",
        "stored.bin": b"STORED-REPLACED",
    }
    additions = (
        (_COMMENTS, b"<comments>one</comments>"),
        (_COMMENTS_RELS, b"<Relationships/>"),
    )
    output = rewrite_raw_zip_members(
        source, replacements=replacements, additions=additions
    )
    _assert_rewrite(source, output, replacements, additions)
    # The new members read as written with the rest of the package.
    with zipfile.ZipFile(io.BytesIO(output)) as archive:
        document = archive.getinfo(_DOCUMENT_PART)
        comments = archive.getinfo(_COMMENTS)
        assert comments.date_time == document.date_time
        assert comments.create_system == document.create_system


def test_a_preamble_trailing_bytes_and_data_descriptors_survive():
    for descriptors, envelope in ((True, False), (False, True)):
        source = _source(descriptors=descriptors, envelope=envelope)
        replacements = {_DOCUMENT_PART: b"<document>edited</document>"}
        additions = ((_COMMENTS, b"<comments/>"),)
        output = rewrite_raw_zip_members(
            source, replacements=replacements, additions=additions
        )
        _assert_rewrite(source, output, replacements, additions)


def test_additions_alone_are_allowed():
    source = _source()
    additions = ((_COMMENTS, b"<comments/>"),)
    output = rewrite_raw_zip_members(source, replacements={}, additions=additions)
    _assert_rewrite(source, output, {}, additions)


def test_one_member_is_the_single_replacement_byte_for_byte():
    source = _source()
    payload = b"<document><body>same edit</body></document>"
    assert replace_raw_zip_member(
        source, filename=_DOCUMENT_PART, payload=payload
    ) == rewrite_raw_zip_members(source, replacements={_DOCUMENT_PART: payload})


def test_the_rewrite_is_deterministic():
    source = _source()
    replacements = {_DOCUMENT_PART: b"<document>x</document>"}
    additions = ((_COMMENTS, b"<comments/>"),)
    first = rewrite_raw_zip_members(
        source, replacements=replacements, additions=additions
    )
    assert first == rewrite_raw_zip_members(
        source, replacements=replacements, additions=additions
    )


@pytest.mark.parametrize(
    "name",
    [
        "WORD/Document.xml",  # collides with an existing member, case folded
        "stored.bin",
        "../escape.xml",
        "word\\comments.xml",
        "/word/comments.xml",
        "word/./comments.xml",
        "word/commënts.xml",  # an appended record carries no UTF-8 flag
        "word/",
        "",
    ],
)
def test_an_appended_name_that_is_unsafe_or_taken_is_refused(name):
    with pytest.raises(RawZipError):
        rewrite_raw_zip_members(
            _source(), replacements={}, additions=((name, b"<x/>"),)
        )


def test_two_appended_members_may_not_share_a_name():
    with pytest.raises(RawZipError, match="collides"):
        rewrite_raw_zip_members(
            _source(),
            replacements={},
            additions=((_COMMENTS, b"<a/>"), ("Word/Comments.XML", b"<b/>")),
        )


def test_an_unsupported_or_missing_member_is_never_replaced():
    source = _write_zip(
        (
            _EntrySpec(_DOCUMENT_PART, b"<document/>"),
            _EntrySpec("odd.bin", b"x" * 64, method=zipfile.ZIP_BZIP2),
        )
    )
    with pytest.raises(RawZipError, match="compression"):
        rewrite_raw_zip_members(
            source,
            replacements={_DOCUMENT_PART: b"<d/>", "odd.bin": b"y"},
        )
    with pytest.raises(RawZipError, match="exactly one"):
        rewrite_raw_zip_members(source, replacements={"missing.xml": b"<m/>"})
    with pytest.raises(ValueError, match="nothing to rewrite"):
        rewrite_raw_zip_members(source, replacements={})


def test_the_audit_refuses_an_output_that_is_not_the_rewrite():
    source = _source()
    replacements = {_DOCUMENT_PART: b"<document>audited</document>"}
    additions = ((_COMMENTS, b"<comments/>"),)
    output = rewrite_raw_zip_members(
        source, replacements=replacements, additions=additions
    )
    audit_raw_zip_rewrite(
        source, output, replacements=replacements, additions=additions
    )
    # A member the caller did not name changed — here, an appended payload
    # the audit was not told about, and a replacement that differs.
    with pytest.raises(RawZipError):
        audit_raw_zip_rewrite(
            source, output, replacements=replacements, additions=()
        )
    with pytest.raises(RawZipError):
        audit_raw_zip_rewrite(
            source,
            output,
            replacements={_DOCUMENT_PART: b"<document>other</document>"},
            additions=additions,
        )
    # An archive rewritten with no additions is not one with them.
    plain = rewrite_raw_zip_members(source, replacements=replacements)
    with pytest.raises(RawZipError):
        audit_raw_zip_rewrite(
            source, plain, replacements=replacements, additions=additions
        )


def test_the_audit_refuses_an_appended_record_of_another_shape():
    """An appended member must be written the one way the writer writes it:
    deflated, general-purpose flags 0, no data descriptor. Setting a flag in
    its local and central records leaves the payload readable — and the
    audit must still refuse it."""
    source = _source()
    additions = ((_COMMENTS, b"<comments/>"),)
    output = bytearray(rewrite_raw_zip_members(source, replacements={}, additions=additions))
    with zipfile.ZipFile(io.BytesIO(bytes(output))) as archive:
        local = archive.getinfo(_COMMENTS).header_offset
    name = _COMMENTS.encode("ascii")
    central = output.index(name, local + 30 + len(name)) - 46
    assert output[local : local + 4] == b"PK\x03\x04"
    assert output[central : central + 4] == b"PK\x01\x02"
    for flags_at in (local + 6, central + 8):
        output[flags_at + 1] |= 0x08  # bit 11: "the name is UTF-8" — harmless, not ours
    tampered = bytes(output)
    with zipfile.ZipFile(io.BytesIO(tampered)) as archive:
        assert archive.read(_COMMENTS) == b"<comments/>"
    with pytest.raises(RawZipError):
        audit_raw_zip_rewrite(source, tampered, replacements={}, additions=additions)


def test_a_cached_source_index_is_reused_and_every_member_rechecked(monkeypatch):
    source = _source()
    real_parse = raw_zip.parse_raw_zip_archive
    parsed: list[bytes] = []

    def tracked(data: bytes, *, mutable_member=_DOCUMENT_PART):
        parsed.append(data)
        return real_parse(data, mutable_member=mutable_member)

    monkeypatch.setattr(raw_zip, "parse_raw_zip_archive", tracked)
    index = raw_zip.parse_raw_zip_archive(source)
    output = raw_zip.rewrite_raw_zip_members(
        source,
        replacements={_DOCUMENT_PART: b"<d/>", "stored.bin": b"s"},
        additions=((_COMMENTS, b"<c/>"),),
        source_archive=index,
    )
    assert parsed == [source, output]  # the source was not parsed again
    # A cached index is still held to the per-member mutation policy.
    bz = _write_zip(
        (
            _EntrySpec(_DOCUMENT_PART, b"<document/>"),
            _EntrySpec("odd.bin", b"x" * 64, method=zipfile.ZIP_BZIP2),
        )
    )
    with pytest.raises(RawZipError, match="compression"):
        rewrite_raw_zip_members(
            bz,
            replacements={_DOCUMENT_PART: b"<d/>", "odd.bin": b"y"},
            source_archive=parse_raw_zip_archive(bz),
        )
