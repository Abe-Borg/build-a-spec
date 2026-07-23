"""Strict lexical byte indexing for source-preserving Word XML edits.

The semantic source map deliberately stores decoded text offsets, not byte
offsets.  This module recomputes byte locality from the immutable source XML
whenever a mutation is considered.  lxml remains the semantic validator; a
separate byte scanner and Expat namespace parse must agree with it before any
offset is trusted.

Only UTF-8 (with or without a UTF-8 BOM) is writable in this first lexical
implementation.  Other encodings remain readable by the ordinary import path
and recoverable through exact-original export, but are mutation blockers.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Iterable
from xml.parsers import expat

from lxml import etree

_UTF8_BOM = b"\xef\xbb\xbf"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W_DOCUMENT = f"{{{_W_NS}}}document"
_W_BODY = f"{{{_W_NS}}}body"
_W_T = f"{{{_W_NS}}}t"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_XMLNS_NS = "http://www.w3.org/2000/xmlns/"
_SPACE = b" \t\r\n"


class XmlLexicalError(ValueError):
    """A source byte span cannot be proven safe for mutation."""

    def __init__(self, blocker: str, detail: str) -> None:
        self.blocker = blocker
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class XmlByteSpan:
    start: int
    end: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.start, bool)
            or isinstance(self.end, bool)
            or not isinstance(self.start, int)
            or not isinstance(self.end, int)
            or self.start < 0
            or self.end < self.start
        ):
            raise ValueError("Invalid XML byte span.")


@dataclass(frozen=True, slots=True)
class WordTextByteSpan:
    body_child_index: int
    text_node_ordinal: int
    element_span: XmlByteSpan
    content_span: XmlByteSpan
    decoded_text: str
    lexical_name: bytes
    mutable_content: bool = True
    blocker: str | None = None


@dataclass(frozen=True, slots=True)
class BodyChildByteSpan:
    body_child_index: int
    expanded_name: str
    element_span: XmlByteSpan


@dataclass(frozen=True, slots=True)
class XmlPatch:
    start: int
    end: int
    replacement: bytes
    uid: str
    reason: str


@dataclass(frozen=True, slots=True)
class SourceXmlIndex:
    encoding: str
    bom: bytes
    xml_declaration_span: XmlByteSpan | None
    document_span: XmlByteSpan
    body_start_tag_span: XmlByteSpan
    body_end_tag_span: XmlByteSpan
    body_content_span: XmlByteSpan
    body_children: tuple[BodyChildByteSpan, ...]
    paragraph_spans: tuple[BodyChildByteSpan, ...]
    word_text_nodes: tuple[WordTextByteSpan, ...]
    body_gaps: tuple[XmlByteSpan, ...]
    body_namespace_bindings: tuple[tuple[str, str], ...]

    def body_child(self, body_child_index: int) -> BodyChildByteSpan:
        for child in self.body_children:
            if child.body_child_index == body_child_index:
                return child
        raise XmlLexicalError(
            "body_anchor_mismatch",
            f"body child {body_child_index} has no proven lexical span",
        )

    def word_text(
        self, body_child_index: int, text_node_ordinal: int
    ) -> WordTextByteSpan:
        for node in self.word_text_nodes:
            if (
                node.body_child_index == body_child_index
                and node.text_node_ordinal == text_node_ordinal
            ):
                return node
        raise XmlLexicalError(
            "text_anchor_mismatch",
            "the mapped Word text node has no proven lexical span",
        )


@dataclass(slots=True)
class _RawAttribute:
    lexical_name: bytes
    raw_value: bytes
    value_start: int


@dataclass(slots=True)
class _Frame:
    lexical_name: bytes
    expanded_name: str
    start: int
    start_tag_end: int
    namespaces: dict[str, str]
    body_child_index: int | None
    direct_body_child: bool
    text_node_ordinal: int | None
    empty: bool = False


@dataclass(slots=True)
class _RawTextRecord:
    body_child_index: int
    text_node_ordinal: int
    element_span: XmlByteSpan
    content_span: XmlByteSpan
    lexical_name: bytes
    mutable_content: bool


@dataclass(slots=True)
class _ScanResult:
    events: list[tuple[str, str, int]]
    document_span: XmlByteSpan | None
    body_start_tag_span: XmlByteSpan | None
    body_end_tag_span: XmlByteSpan | None
    body_content_span: XmlByteSpan | None
    body_children: list[BodyChildByteSpan]
    word_text_nodes: list[_RawTextRecord]
    body_namespaces: dict[str, str] | None


def _unsafe(detail: str) -> XmlLexicalError:
    return XmlLexicalError("unsafe_document_xml", detail)


def _unsupported_encoding(detail: str) -> XmlLexicalError:
    return XmlLexicalError("unsupported_source_xml_encoding", detail)


def _skip_space(data: bytes, position: int, limit: int) -> int:
    while position < limit and data[position] in _SPACE:
        position += 1
    return position


def _read_name(data: bytes, position: int, limit: int) -> tuple[bytes, int]:
    start = position
    terminators = b" \t\r\n/>=?"
    while position < limit and data[position] not in terminators:
        if data[position] in b"<'\"&":
            break
        position += 1
    if position == start:
        raise _unsafe("an XML name is missing")
    raw = data[start:position]
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _unsafe("an XML name is not valid UTF-8") from exc
    return raw, position


def _qname_parts(raw_name: bytes) -> tuple[str, str]:
    try:
        name = raw_name.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - checked by reader
        raise _unsafe("an XML name is not valid UTF-8") from exc
    pieces = name.split(":")
    if len(pieces) == 1 and pieces[0]:
        return "", pieces[0]
    if len(pieces) == 2 and pieces[0] and pieces[1]:
        return pieces[0], pieces[1]
    raise _unsafe("an XML qualified name is ambiguous")


def _expanded_name(
    raw_name: bytes,
    namespaces: dict[str, str],
    *,
    attribute: bool = False,
) -> str:
    prefix, local = _qname_parts(raw_name)
    if prefix:
        namespace = namespaces.get(prefix)
        if not namespace:
            raise _unsafe(f"the XML prefix {prefix!r} is not bound")
        return f"{{{namespace}}}{local}"
    if attribute:
        return local
    namespace = namespaces.get("", "")
    return f"{{{namespace}}}{local}" if namespace else local


def _xml_character_allowed(value: int) -> bool:
    return (
        value in {0x9, 0xA, 0xD}
        or 0x20 <= value <= 0xD7FF
        or 0xE000 <= value <= 0xFFFD
        or 0x10000 <= value <= 0x10FFFF
    )


def _decode_reference(token: bytes) -> str:
    predefined = {
        b"amp": "&",
        b"lt": "<",
        b"gt": ">",
        b"quot": '"',
        b"apos": "'",
    }
    if token in predefined:
        return predefined[token]
    base = 10
    digits = b""
    if token.startswith(b"#x"):
        base = 16
        digits = token[2:]
        valid = b"0123456789abcdefABCDEF"
    elif token.startswith(b"#"):
        digits = token[1:]
        valid = b"0123456789"
    else:
        raise _unsafe("a custom or unsupported XML entity was encountered")
    if not digits or any(value not in valid for value in digits):
        raise _unsafe("an XML character reference is malformed")
    try:
        value = int(digits, base)
    except ValueError as exc:  # pragma: no cover - guarded above
        raise _unsafe("an XML character reference is malformed") from exc
    if not _xml_character_allowed(value):
        raise _unsafe("an XML character reference is not legal in XML 1.0")
    return chr(value)


def _utf8_character_length(first: int) -> int:
    if first < 0x80:
        return 1
    if 0xC2 <= first <= 0xDF:
        return 2
    if 0xE0 <= first <= 0xEF:
        return 3
    if 0xF0 <= first <= 0xF4:
        return 4
    raise _unsafe("source XML contains invalid UTF-8")


def _decode_text_with_boundaries(
    raw: bytes,
    *,
    absolute_start: int,
    attribute: bool = False,
) -> tuple[str, tuple[int, ...]]:
    """Decode ordinary XML character data and retain each raw boundary."""
    decoded: list[str] = []
    boundaries = [absolute_start]
    position = 0
    while position < len(raw):
        value = raw[position]
        if value == ord("<"):
            raise XmlLexicalError(
                "unsupported_source_text_lexical_form",
                "mutable Word text must use ordinary character data, not markup or CDATA",
            )
        if value == ord("&"):
            end = raw.find(b";", position + 1)
            if end < 0:
                raise _unsafe("an XML entity reference is unterminated")
            character = _decode_reference(raw[position + 1 : end])
            position = end + 1
        elif value == 0x0D:
            if position + 1 < len(raw) and raw[position + 1] == 0x0A:
                position += 2
            else:
                position += 1
            character = " " if attribute else "\n"
        elif value in {0x09, 0x0A} and attribute:
            position += 1
            character = " "
        else:
            length = _utf8_character_length(value)
            end = position + length
            try:
                character = raw[position:end].decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise _unsafe("source XML contains invalid UTF-8") from exc
            if len(character) != 1 or not _xml_character_allowed(ord(character)):
                raise _unsafe("source XML contains an illegal XML character")
            position = end
        decoded.append(character)
        boundaries.append(absolute_start + position)
    return "".join(decoded), tuple(boundaries)


def _parse_declaration(
    data: bytes, start: int, end: int
) -> tuple[tuple[str, str], ...]:
    position = start + len(b"<?xml")
    content_end = end - len(b"?>")
    if position >= content_end or data[position] not in _SPACE:
        raise _unsafe("the XML declaration is malformed")
    attributes: list[tuple[str, str]] = []
    while True:
        position = _skip_space(data, position, content_end)
        if position == content_end:
            break
        raw_name, position = _read_name(data, position, content_end)
        try:
            name = raw_name.decode("ascii")
        except UnicodeDecodeError as exc:
            raise _unsafe("the XML declaration is malformed") from exc
        position = _skip_space(data, position, content_end)
        if position >= content_end or data[position] != ord("="):
            raise _unsafe("the XML declaration is malformed")
        position = _skip_space(data, position + 1, content_end)
        if position >= content_end or data[position] not in {ord("'"), ord('"')}:
            raise _unsafe("the XML declaration is malformed")
        quote = data[position]
        value_start = position + 1
        value_end = data.find(bytes((quote,)), value_start, content_end)
        if value_end < 0:
            raise _unsafe("the XML declaration is malformed")
        try:
            value = data[value_start:value_end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise _unsafe("the XML declaration is malformed") from exc
        attributes.append((name, value))
        position = value_end + 1

    names = [name for name, _value in attributes]
    if (
        not names
        or names[0] != "version"
        or len(names) != len(set(names))
        or any(name not in {"version", "encoding", "standalone"} for name in names)
        or ("encoding" in names and names.index("encoding") != 1)
        or (
            "standalone" in names
            and names.index("standalone") != len(names) - 1
        )
    ):
        raise _unsafe("the XML declaration pseudo-attributes are malformed")
    values = dict(attributes)
    if values["version"] != "1.0":
        raise _unsafe("only XML 1.0 Word documents are supported")
    if values.get("standalone") not in {None, "yes", "no"}:
        raise _unsafe("the XML standalone declaration is malformed")
    return tuple(attributes)


def _encoding_info(
    document_xml: bytes,
) -> tuple[str, bytes, XmlByteSpan | None]:
    if not isinstance(document_xml, bytes):
        raise TypeError("document_xml must be bytes")
    if not document_xml:
        raise _unsafe("the main document XML is empty")

    unsupported_boms = (
        b"\x00\x00\xfe\xff",
        b"\xff\xfe\x00\x00",
        b"\xfe\xff",
        b"\xff\xfe",
    )
    if any(document_xml.startswith(marker) for marker in unsupported_boms):
        raise _unsupported_encoding(
            "source-preserving mutation currently supports only UTF-8 Word XML"
        )
    # A BOM-less UTF-16/32 document need not begin with ``<?xml``.  Valid
    # XML may open directly with the document element (or XML whitespace),
    # so recognize the interleaved NUL family before looking for the narrower
    # declaration signatures.  NUL is never legal in UTF-8 XML.
    if b"\x00" in document_xml[:4]:
        raise _unsupported_encoding(
            "source-preserving mutation currently supports only UTF-8 Word XML"
        )
    signatures = (
        b"\x00\x00\x00<",
        b"<\x00\x00\x00",
        b"\x00<\x00?",
        b"<\x00?\x00",
        b"Lo\xa7\x94",  # EBCDIC XML autodetection signature
    )
    if any(document_xml.startswith(marker) for marker in signatures):
        raise _unsupported_encoding(
            "source-preserving mutation currently supports only UTF-8 Word XML"
        )

    bom = _UTF8_BOM if document_xml.startswith(_UTF8_BOM) else b""
    start = len(bom)
    declaration_span: XmlByteSpan | None = None
    if document_xml.startswith(b"<?xml", start) and (
        start + 5 < len(document_xml)
        and document_xml[start + 5] in _SPACE
    ):
        end_marker = document_xml.find(b"?>", start + 5)
        if end_marker < 0:
            raise _unsafe("the XML declaration is unterminated")
        declaration_span = XmlByteSpan(start, end_marker + 2)
        declaration = _parse_declaration(
            document_xml, declaration_span.start, declaration_span.end
        )
        declared_encoding = dict(declaration).get("encoding")
        if declared_encoding is not None and declared_encoding.casefold() != "utf-8":
            raise _unsupported_encoding(
                f"the source Word XML encoding {declared_encoding!r} is not writable"
            )
    return "utf-8", bom, declaration_span


def detect_xml_encoding(document_xml: bytes) -> str:
    """Return the canonical writable encoding or raise a precise blocker."""
    encoding, _bom, _declaration = _encoding_info(document_xml)
    return encoding


def _parse_start_tag(
    data: bytes, start: int
) -> tuple[bytes, list[_RawAttribute], int, bool]:
    position = start + 1
    raw_name, position = _read_name(data, position, len(data))
    attributes: list[_RawAttribute] = []
    while True:
        position = _skip_space(data, position, len(data))
        if position >= len(data):
            raise _unsafe("an XML start tag is unterminated")
        if data[position] == ord(">"):
            return raw_name, attributes, position + 1, False
        if (
            data[position] == ord("/")
            and position + 1 < len(data)
            and data[position + 1] == ord(">")
        ):
            return raw_name, attributes, position + 2, True

        attr_name, position = _read_name(data, position, len(data))
        position = _skip_space(data, position, len(data))
        if position >= len(data) or data[position] != ord("="):
            raise _unsafe("an XML attribute is missing '='")
        position = _skip_space(data, position + 1, len(data))
        if position >= len(data) or data[position] not in {ord("'"), ord('"')}:
            raise _unsafe("an XML attribute value is not quoted")
        quote = data[position]
        value_start = position + 1
        value_end = data.find(bytes((quote,)), value_start)
        if value_end < 0:
            raise _unsafe("an XML attribute value is unterminated")
        if b"<" in data[value_start:value_end]:
            raise _unsafe("an XML attribute contains a literal '<'")
        attributes.append(
            _RawAttribute(
                lexical_name=attr_name,
                raw_value=data[value_start:value_end],
                value_start=value_start,
            )
        )
        position = value_end + 1


def _parse_end_tag(data: bytes, start: int) -> tuple[bytes, int]:
    position = start + 2
    raw_name, position = _read_name(data, position, len(data))
    position = _skip_space(data, position, len(data))
    if position >= len(data) or data[position] != ord(">"):
        raise _unsafe("an XML end tag is malformed")
    return raw_name, position + 1


def _scanner(document_xml: bytes, bom: bytes) -> _ScanResult:
    result = _ScanResult([], None, None, None, None, [], [], None)
    stack: list[_Frame] = []
    body_child_count = 0
    text_counts: dict[int, int] = {}
    root_count = 0
    position = len(bom)

    def finish(frame: _Frame, end_start: int, end: int) -> None:
        nonlocal result
        element_span = XmlByteSpan(frame.start, end)
        if frame.direct_body_child:
            if frame.body_child_index is None:  # pragma: no cover - invariant
                raise _unsafe("a direct body child lost its lexical index")
            result.body_children.append(
                BodyChildByteSpan(
                    frame.body_child_index,
                    frame.expanded_name,
                    element_span,
                )
            )
        if frame.expanded_name == _W_T and frame.body_child_index is not None:
            if frame.text_node_ordinal is None:  # pragma: no cover - invariant
                raise _unsafe("a Word text node lost its lexical ordinal")
            if frame.empty:
                content_span = XmlByteSpan(frame.start_tag_end, frame.start_tag_end)
                mutable = False
            else:
                content_span = XmlByteSpan(frame.start_tag_end, end_start)
                mutable = b"<" not in document_xml[
                    content_span.start : content_span.end
                ]
            result.word_text_nodes.append(
                _RawTextRecord(
                    frame.body_child_index,
                    frame.text_node_ordinal,
                    element_span,
                    content_span,
                    frame.lexical_name,
                    mutable,
                )
            )
        if frame.expanded_name == _W_BODY:
            if frame.empty:
                raise _unsafe("the Word body cannot be self-closing")
            if result.body_end_tag_span is not None:
                raise _unsafe("the source contains more than one Word body")
            result.body_end_tag_span = XmlByteSpan(end_start, end)
            result.body_content_span = XmlByteSpan(frame.start_tag_end, end_start)
        if frame.expanded_name == _W_DOCUMENT:
            if result.document_span is not None:
                raise _unsafe("the source contains more than one Word document")
            result.document_span = element_span

    while position < len(document_xml):
        if document_xml[position] != ord("<"):
            next_markup = document_xml.find(b"<", position)
            if next_markup < 0:
                next_markup = len(document_xml)
            _decode_text_with_boundaries(
                document_xml[position:next_markup], absolute_start=position
            )
            position = next_markup
            continue

        if document_xml.startswith(b"<!--", position):
            end_marker = document_xml.find(b"-->", position + 4)
            if end_marker < 0:
                raise _unsafe("an XML comment is unterminated")
            position = end_marker + 3
            continue
        if document_xml.startswith(b"<?", position):
            end_marker = document_xml.find(b"?>", position + 2)
            if end_marker < 0:
                raise _unsafe("an XML processing instruction is unterminated")
            position = end_marker + 2
            continue
        if document_xml.startswith(b"<![CDATA[", position):
            if not stack:
                raise _unsafe("CDATA appears outside the document element")
            end_marker = document_xml.find(b"]]>", position + 9)
            if end_marker < 0:
                raise _unsafe("an XML CDATA section is unterminated")
            try:
                document_xml[position + 9 : end_marker].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _unsafe("an XML CDATA section is not valid UTF-8") from exc
            position = end_marker + 3
            continue
        if document_xml.startswith(b"<!", position):
            raise _unsafe("DTD and declaration markup is not accepted")
        if document_xml.startswith(b"</", position):
            raw_name, end = _parse_end_tag(document_xml, position)
            if not stack:
                raise _unsafe("an XML end tag has no matching start tag")
            frame = stack.pop()
            if raw_name != frame.lexical_name:
                raise _unsafe("an XML end tag does not match its start tag")
            result.events.append(("end", frame.expanded_name, len(stack)))
            finish(frame, position, end)
            position = end
            continue

        raw_name, attributes, end, empty = _parse_start_tag(
            document_xml, position
        )
        namespaces = (
            dict(stack[-1].namespaces)
            if stack
            else {"xml": _XML_NS}
        )
        decoded_attribute_values = [
            _decode_text_with_boundaries(
                attribute.raw_value,
                absolute_start=attribute.value_start,
                attribute=True,
            )[0]
            for attribute in attributes
        ]
        for attribute, uri in zip(attributes, decoded_attribute_values):
            prefix, local = _qname_parts(attribute.lexical_name)
            if not prefix and local == "xmlns":
                declared_prefix = ""
            elif prefix == "xmlns":
                declared_prefix = local
            else:
                continue
            if declared_prefix == "xml" and uri != _XML_NS:
                raise _unsafe("the reserved xml prefix was rebound")
            if declared_prefix == "xmlns":
                raise _unsafe("the reserved xmlns prefix was rebound")
            if uri == _XMLNS_NS:
                raise _unsafe("the reserved xmlns namespace was rebound")
            if uri:
                namespaces[declared_prefix] = uri
            else:
                namespaces.pop(declared_prefix, None)

        expanded = _expanded_name(raw_name, namespaces)
        parent = stack[-1] if stack else None
        direct_body_child = bool(parent and parent.expanded_name == _W_BODY)
        if direct_body_child:
            body_child_index = body_child_count
            body_child_count += 1
        else:
            body_child_index = parent.body_child_index if parent else None
        text_ordinal: int | None = None
        if expanded == _W_T and body_child_index is not None:
            text_ordinal = text_counts.get(body_child_index, 0)
            text_counts[body_child_index] = text_ordinal + 1

        frame = _Frame(
            lexical_name=raw_name,
            expanded_name=expanded,
            start=position,
            start_tag_end=end,
            namespaces=namespaces,
            body_child_index=body_child_index,
            direct_body_child=direct_body_child,
            text_node_ordinal=text_ordinal,
            empty=empty,
        )
        result.events.append(("start", expanded, len(stack)))
        if not stack:
            root_count += 1
        if expanded == _W_BODY:
            if result.body_start_tag_span is not None:
                raise _unsafe("the source contains more than one Word body")
            result.body_start_tag_span = XmlByteSpan(position, end)
            result.body_namespaces = dict(namespaces)

        if empty:
            result.events.append(("end", expanded, len(stack)))
            finish(frame, end, end)
        else:
            stack.append(frame)
        position = end

    if stack:
        raise _unsafe("an XML element is unterminated")
    if root_count != 1:
        raise _unsafe("the source does not contain exactly one document element")
    return result


def _canonical_expat_name(name: str) -> str:
    separator = "\x1f"
    if separator not in name:
        return name
    namespace, local = name.split(separator, 1)
    return f"{{{namespace}}}{local}"


def _expat_events(document_xml: bytes) -> list[tuple[str, str, int]]:
    parser = expat.ParserCreate(namespace_separator="\x1f")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    events: list[tuple[str, str, int]] = []
    stack: list[str] = []

    def start(name: str, _attributes) -> None:
        expanded = _canonical_expat_name(name)
        events.append(("start", expanded, len(stack)))
        stack.append(expanded)

    def end(name: str) -> None:
        expanded = _canonical_expat_name(name)
        if not stack or stack[-1] != expanded:
            raise _unsafe("Expat reported inconsistent XML nesting")
        stack.pop()
        events.append(("end", expanded, len(stack)))

    def reject(*_args) -> None:
        raise _unsafe("DTD and entity declarations are not accepted")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.UnparsedEntityDeclHandler = reject
    parser.NotationDeclHandler = reject
    parser.ExternalEntityRefHandler = lambda *_args: 0
    try:
        parser.Parse(document_xml, True)
    except XmlLexicalError:
        raise
    except (expat.ExpatError, UnicodeError, ValueError) as exc:
        raise _unsafe("Expat rejected the source Word XML") from exc
    if stack:  # pragma: no cover - Expat enforces this
        raise _unsafe("Expat reported an unterminated XML element")
    return events


def _lxml_tree(document_xml: bytes, validated_tree=None):
    if validated_tree is not None:
        tree = validated_tree
    else:
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            remove_blank_text=False,
            recover=False,
        )
        try:
            tree = etree.parse(BytesIO(document_xml), parser=parser)
        except (etree.XMLSyntaxError, ValueError) as exc:
            raise _unsafe("lxml rejected the source Word XML") from exc
    if tree.docinfo.doctype:
        raise _unsafe("DTD-bearing Word XML is not accepted")
    return tree


def _lxml_events(root) -> list[tuple[str, str, int]]:
    events: list[tuple[str, str, int]] = []

    def visit(element, depth: int) -> None:
        if not isinstance(element.tag, str):
            return
        events.append(("start", element.tag, depth))
        for child in element.iterchildren():
            if isinstance(child.tag, str):
                visit(child, depth + 1)
        events.append(("end", element.tag, depth))

    visit(root, 0)
    return events


def build_source_xml_index(
    document_xml: bytes,
    *,
    validated_tree=None,
) -> SourceXmlIndex:
    """Build a byte index only when scanner, Expat, and lxml all agree."""
    encoding, bom, declaration_span = _encoding_info(document_xml)
    tree = _lxml_tree(document_xml, validated_tree)
    scan = _scanner(document_xml, bom)
    expat_events = _expat_events(document_xml)
    root = tree.getroot()
    lxml_events = _lxml_events(root)
    if scan.events != expat_events or scan.events != lxml_events:
        raise _unsafe("independent XML parsers disagree on element names or nesting")
    if root.tag != _W_DOCUMENT:
        raise _unsafe("the main part is not a supported Word document element")

    bodies = [
        element
        for element in root.iter()
        if isinstance(element.tag, str) and element.tag == _W_BODY
    ]
    if len(bodies) != 1 or bodies[0].getparent() is not root:
        raise _unsafe("the source does not contain one direct Word body")
    body = bodies[0]
    lxml_children = [
        child for child in body.iterchildren() if isinstance(child.tag, str)
    ]
    scan.body_children.sort(key=lambda child: child.body_child_index)
    if [child.expanded_name for child in scan.body_children] != [
        child.tag for child in lxml_children
    ]:
        raise _unsafe("the lexical and semantic Word body inventories disagree")

    lxml_text: dict[tuple[int, int], str] = {}
    for body_child_index, child in enumerate(lxml_children):
        ordinal = 0
        for node in child.iter():
            if node is child or not isinstance(node.tag, str) or node.tag != _W_T:
                continue
            lxml_text[(body_child_index, ordinal)] = node.text or ""
            ordinal += 1

    word_text_nodes: list[WordTextByteSpan] = []
    scan.word_text_nodes.sort(
        key=lambda node: (node.body_child_index, node.text_node_ordinal)
    )
    for raw_node in scan.word_text_nodes:
        key = (raw_node.body_child_index, raw_node.text_node_ordinal)
        if key not in lxml_text:
            raise _unsafe("the lexical and semantic Word text inventories disagree")
        decoded_text = lxml_text[key]
        blocker: str | None = None
        if raw_node.mutable_content:
            decoded, _boundaries = _decode_text_with_boundaries(
                document_xml[
                    raw_node.content_span.start : raw_node.content_span.end
                ],
                absolute_start=raw_node.content_span.start,
            )
            if decoded != decoded_text:
                raise _unsafe("the lexical and semantic Word text values disagree")
        else:
            blocker = "unsupported_source_text_lexical_form"
        word_text_nodes.append(
            WordTextByteSpan(
                body_child_index=raw_node.body_child_index,
                text_node_ordinal=raw_node.text_node_ordinal,
                element_span=raw_node.element_span,
                content_span=raw_node.content_span,
                decoded_text=decoded_text,
                lexical_name=raw_node.lexical_name,
                mutable_content=raw_node.mutable_content,
                blocker=blocker,
            )
        )
    if len(lxml_text) != len(word_text_nodes):
        raise _unsafe("the lexical and semantic Word text inventories disagree")

    required = (
        scan.document_span,
        scan.body_start_tag_span,
        scan.body_end_tag_span,
        scan.body_content_span,
    )
    if any(value is None for value in required) or scan.body_namespaces is None:
        raise _unsafe("the Word document or body has no proven lexical span")
    document_span = scan.document_span
    body_start = scan.body_start_tag_span
    body_end = scan.body_end_tag_span
    body_content = scan.body_content_span
    assert document_span is not None
    assert body_start is not None
    assert body_end is not None
    assert body_content is not None

    gaps: list[XmlByteSpan] = []
    cursor = body_content.start
    for child in scan.body_children:
        if child.element_span.start < cursor:
            raise _unsafe("direct Word body byte spans overlap")
        gaps.append(XmlByteSpan(cursor, child.element_span.start))
        cursor = child.element_span.end
    gaps.append(XmlByteSpan(cursor, body_content.end))

    paragraphs = tuple(
        child
        for child in scan.body_children
        if child.expanded_name == f"{{{_W_NS}}}p"
    )
    return SourceXmlIndex(
        encoding=encoding,
        bom=bom,
        xml_declaration_span=declaration_span,
        document_span=document_span,
        body_start_tag_span=body_start,
        body_end_tag_span=body_end,
        body_content_span=body_content,
        body_children=tuple(scan.body_children),
        paragraph_spans=paragraphs,
        word_text_nodes=tuple(word_text_nodes),
        body_gaps=tuple(gaps),
        body_namespace_bindings=tuple(sorted(scan.body_namespaces.items())),
    )


def decoded_slice_byte_span(
    document_xml: bytes,
    node: WordTextByteSpan,
    start: int,
    end: int,
) -> XmlByteSpan:
    """Map decoded Python-string offsets to exact raw XML byte offsets."""
    if not node.mutable_content:
        raise XmlLexicalError(
            node.blocker or "unsupported_source_text_lexical_form",
            "the mapped Word text uses a lexical form that cannot be spliced safely",
        )
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not 0 <= start <= end <= len(node.decoded_text)
    ):
        raise XmlLexicalError(
            "text_anchor_mismatch", "the mapped decoded text offsets are invalid"
        )
    raw = document_xml[node.content_span.start : node.content_span.end]
    decoded, boundaries = _decode_text_with_boundaries(
        raw, absolute_start=node.content_span.start
    )
    if decoded != node.decoded_text or len(boundaries) != len(decoded) + 1:
        raise XmlLexicalError(
            "text_anchor_mismatch",
            "the mapped Word text no longer matches its lexical byte span",
        )
    return XmlByteSpan(boundaries[start], boundaries[end])


def encode_word_text(
    text: str,
    *,
    raw_prefix: bytes = b"",
    raw_suffix: bytes = b"",
) -> bytes:
    """Encode new ordinary ``w:t`` character data without touching markup."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(raw_prefix, bytes) or not isinstance(raw_suffix, bytes):
        raise TypeError("raw XML context must be bytes")
    output: list[bytes] = []
    raw_tail = raw_prefix[-2:]
    for character in text:
        value = ord(character)
        if not _xml_character_allowed(value):
            raise XmlLexicalError(
                "invalid_xml_character",
                "the replacement contains a character XML cannot represent",
            )
        if character in "\t\r\n":
            raise XmlLexicalError(
                "unsupported_text_control",
                "tabs and line breaks require unsupported Word run markup",
            )
        if character == "&":
            encoded = b"&amp;"
        elif character == "<":
            encoded = b"&lt;"
        elif character == ">" and raw_tail.endswith(b"]]"):
            encoded = b"&gt;"
        else:
            encoded = character.encode("utf-8")
        output.append(encoded)
        raw_tail = (raw_tail + encoded)[-2:]

    # A literal '>' may be the first untouched suffix byte. If the new text
    # ends in two raw closing brackets, encode its final bracket numerically
    # so the splice cannot form the forbidden character-data token ``]]>``
    # across the right patch boundary.
    if raw_suffix.startswith(b">") and raw_tail.endswith(b"]]"):
        for output_index in range(len(output) - 1, -1, -1):
            if output[output_index] == b"]":
                output[output_index] = b"&#93;"
                break
        else:  # pragma: no cover - a valid non-empty source slice prevents it
            raise XmlLexicalError(
                "unsupported_source_text_lexical_form",
                "the replacement cannot be separated safely from its source suffix",
            )
    return b"".join(output)


def apply_xml_patches(source: bytes, patches: Iterable[XmlPatch]) -> bytes:
    """Apply a disjoint manifest while copying every unpatched byte exactly."""
    if not isinstance(source, bytes):
        raise TypeError("source must be bytes")
    ordered = sorted(tuple(patches), key=lambda patch: (patch.start, patch.end))
    previous: XmlPatch | None = None
    for patch in ordered:
        if (
            isinstance(patch.start, bool)
            or isinstance(patch.end, bool)
            or not isinstance(patch.start, int)
            or not isinstance(patch.end, int)
            or not 0 <= patch.start <= patch.end <= len(source)
            or not isinstance(patch.replacement, bytes)
        ):
            raise XmlLexicalError(
                "invalid_xml_patch", "an XML byte patch is malformed"
            )
        if previous is not None and (
            patch.start < previous.end
            or (
                patch.start == previous.start
                and (patch.start == patch.end or previous.start == previous.end)
            )
        ):
            raise XmlLexicalError(
                "overlapping_xml_patches",
                "approved XML byte patches overlap or have ambiguous ordering",
            )
        previous = patch

    if not ordered:
        return source
    output: list[bytes] = []
    cursor = 0
    for patch in ordered:
        output.append(source[cursor : patch.start])
        output.append(patch.replacement)
        cursor = patch.end
    output.append(source[cursor:])
    return b"".join(output)


__all__ = [
    "BodyChildByteSpan",
    "SourceXmlIndex",
    "WordTextByteSpan",
    "XmlByteSpan",
    "XmlLexicalError",
    "XmlPatch",
    "apply_xml_patches",
    "build_source_xml_index",
    "decoded_slice_byte_span",
    "detect_xml_encoding",
    "encode_word_text",
]
