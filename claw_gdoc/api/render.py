"""Document editing and rendering helpers."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from .schemas import (
    Body,
    Bullet,
    DocumentResource,
    DocumentTabResource,
    NamedStyles,
    Paragraph,
    ParagraphElement,
    ParagraphStyle,
    ResponseItem,
    SectionBreak,
    StructuralElement,
    TabResource,
    TextRun,
    TextStyle,
)

SUPPORTED_TEXT_STYLE_FIELDS = {
    "backgroundColor",
    "baselineOffset",
    "bold",
    "fontSize",
    "foregroundColor",
    "italic",
    "link",
    "smallCaps",
    "strikethrough",
    "underline",
    "weightedFontFamily",
}
SUPPORTED_PARAGRAPH_STYLE_FIELDS = {
    "direction",
    "indentFirstLine",
    "indentStart",
    "namedStyleType",
}
SUPPORTED_DOCUMENT_STYLE_FIELDS = {
    "background",
    "documentFormat",
    "marginBottom",
    "marginFooter",
    "marginHeader",
    "marginLeft",
    "marginRight",
    "marginTop",
    "pageNumberStart",
    "pageSize",
    "useCustomHeaderFooterMargins",
}


def normalize_body_text(text: str | None) -> str:
    text = text or ""
    if not text.endswith("\n"):
        text += "\n"
    if not text:
        return "\n"
    return text


def default_named_styles() -> dict[str, Any]:
    return {
        "styles": [
            {
                "namedStyleType": "NORMAL_TEXT",
                "paragraphStyle": {
                    "alignment": "START",
                    "direction": "LEFT_TO_RIGHT",
                    "lineSpacing": 115,
                    "namedStyleType": "NORMAL_TEXT",
                    "spacingMode": "COLLAPSE_LISTS",
                },
                "textStyle": {
                    "backgroundColor": {},
                    "baselineOffset": "NONE",
                    "bold": False,
                    "fontSize": {"magnitude": 11, "unit": "PT"},
                    "foregroundColor": {"color": {"rgbColor": {}}},
                    "italic": False,
                    "smallCaps": False,
                    "strikethrough": False,
                    "underline": False,
                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 400},
                },
            },
            {
                "namedStyleType": "HEADING_1",
                "paragraphStyle": {
                    "direction": "LEFT_TO_RIGHT",
                    "keepLinesTogether": True,
                    "keepWithNext": True,
                    "spaceAbove": {"magnitude": 20, "unit": "PT"},
                    "spaceBelow": {"magnitude": 6, "unit": "PT"},
                },
                "textStyle": {
                    "bold": False,
                    "fontSize": {"magnitude": 20, "unit": "PT"},
                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 400},
                },
            },
            {
                "namedStyleType": "HEADING_2",
                "paragraphStyle": {
                    "direction": "LEFT_TO_RIGHT",
                    "keepLinesTogether": True,
                    "keepWithNext": True,
                    "spaceAbove": {"magnitude": 18, "unit": "PT"},
                    "spaceBelow": {"magnitude": 6, "unit": "PT"},
                },
                "textStyle": {
                    "bold": False,
                    "fontSize": {"magnitude": 16, "unit": "PT"},
                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 400},
                },
            },
            {
                "namedStyleType": "HEADING_3",
                "paragraphStyle": {
                    "direction": "LEFT_TO_RIGHT",
                    "keepLinesTogether": True,
                    "keepWithNext": True,
                    "spaceAbove": {"magnitude": 16, "unit": "PT"},
                    "spaceBelow": {"magnitude": 4, "unit": "PT"},
                },
                "textStyle": {
                    "bold": False,
                    "fontSize": {"magnitude": 14, "unit": "PT"},
                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 400},
                },
            },
        ]
    }


def default_document_style() -> dict[str, Any]:
    return {
        "background": {"color": {}},
        "documentFormat": {"documentMode": "PAGES"},
        "marginBottom": {"magnitude": 72, "unit": "PT"},
        "marginFooter": {"magnitude": 36, "unit": "PT"},
        "marginHeader": {"magnitude": 36, "unit": "PT"},
        "marginLeft": {"magnitude": 72, "unit": "PT"},
        "marginRight": {"magnitude": 72, "unit": "PT"},
        "marginTop": {"magnitude": 72, "unit": "PT"},
        "pageNumberStart": 1,
        "pageSize": {
            "height": {"magnitude": 792, "unit": "PT"},
            "width": {"magnitude": 612, "unit": "PT"},
        },
        "useCustomHeaderFooterMargins": True,
    }


def load_json_field(raw: str, fallback):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return fallback
    return value if value is not None else fallback


def dump_json_field(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _style_for_index(spans: list[dict], index: int) -> dict[str, Any]:
    style: dict[str, Any] = {}
    for span in spans:
        if span["startIndex"] <= index < span["endIndex"]:
            style.update(span.get("textStyle", {}))
    return style


def _style_model(style: dict[str, Any]) -> TextStyle:
    link = style.get("link")
    payload = dict(style)
    if isinstance(link, str):
        payload["link"] = {"url": link}
    return TextStyle.model_validate(payload)


def _paragraph_meta(paragraph_ops: list[dict], start_index: int, end_index: int) -> dict[str, Any]:
    meta: dict[str, Any] = {"direction": "LEFT_TO_RIGHT", "namedStyleType": "NORMAL_TEXT"}
    bullet_list_id = None
    for entry in paragraph_ops:
        overlaps = entry["startIndex"] < end_index and entry["endIndex"] > start_index
        if not overlaps:
            continue
        style = entry.get("paragraphStyle", {})
        for field in SUPPORTED_PARAGRAPH_STYLE_FIELDS:
            value = style.get(field)
            if value is not None:
                meta[field] = value
        if entry.get("namedStyleType"):
            meta["namedStyleType"] = entry["namedStyleType"]
        if entry.get("bulletPreset"):
            bullet_list_id = entry.get("listId") or f"list-{start_index}"
    if bullet_list_id:
        meta.setdefault("indentFirstLine", {"magnitude": 18, "unit": "PT"})
        meta.setdefault("indentStart", {"magnitude": 36, "unit": "PT"})
        meta["bullet"] = Bullet(
            listId=bullet_list_id,
            textStyle=TextStyle(underline=False),
        )
    if str(meta.get("namedStyleType", "")).startswith("HEADING_"):
        meta.setdefault("headingId", f"h.mock.{start_index}")
    return meta


def _list_payload(paragraph_ops: list[dict]) -> dict[str, dict[str, Any]]:
    lists: dict[str, dict[str, Any]] = {}
    for entry in paragraph_ops:
        if entry.get("bulletPreset"):
            list_id = entry.get("listId") or "list-default"
            lists[list_id] = {
                "listProperties": {
                    "nestingLevels": [
                        {
                            "glyphType": "BULLET",
                            "glyphSymbol": "\u2022",
                        }
                    ]
                }
            }
    return lists


def render_document_resource(
    *,
    document_id: str,
    title: str,
    body_text: str,
    text_style_spans_json: str,
    paragraph_style_json: str,
    named_styles_json: str,
    document_style_json: str,
    revision_id: int | str,
    include_tabs_content: bool = False,
    include_tabs: bool = False,
    suggestions_view_mode: str = "SUGGESTIONS_INLINE",
) -> DocumentResource:
    text = normalize_body_text(body_text)
    text_spans = load_json_field(text_style_spans_json, [])
    paragraph_ops = load_json_field(paragraph_style_json, [])
    named_styles = load_json_field(named_styles_json, default_named_styles())
    document_style = load_json_field(document_style_json, default_document_style())

    content: list[StructuralElement] = [
        StructuralElement(
            endIndex=1,
            sectionBreak=SectionBreak(
                sectionStyle={
                    "columnSeparatorStyle": "NONE",
                    "contentDirection": "LEFT_TO_RIGHT",
                    "sectionType": "CONTINUOUS",
                }
            ),
        )
    ]

    start_index = 1
    cursor = 0
    for chunk in text.splitlines(keepends=True):
        paragraph_start = start_index
        paragraph_end = paragraph_start + len(chunk)
        boundaries = {paragraph_start, paragraph_end}
        for span in text_spans:
            if span["startIndex"] < paragraph_end and span["endIndex"] > paragraph_start:
                boundaries.add(max(paragraph_start, span["startIndex"]))
                boundaries.add(min(paragraph_end, span["endIndex"]))
        ordered = sorted(boundaries)

        elements: list[ParagraphElement] = []
        for part_start, part_end in zip(ordered, ordered[1:]):
            raw = text[part_start - 1 : part_end - 1]
            style = _style_for_index(text_spans, part_start)
            elements.append(
                ParagraphElement(
                    startIndex=part_start,
                    endIndex=part_end,
                    textRun=TextRun(
                        content=raw,
                        textStyle=_style_model(style),
                    ),
                )
            )

        meta = _paragraph_meta(paragraph_ops, paragraph_start, paragraph_end)
        content.append(
            StructuralElement(
                startIndex=paragraph_start,
                endIndex=paragraph_end,
                paragraph=Paragraph(
                    elements=elements,
                    paragraphStyle=ParagraphStyle.model_validate(
                        {
                            "direction": meta.get("direction", "LEFT_TO_RIGHT"),
                            "namedStyleType": meta["namedStyleType"],
                            "headingId": meta.get("headingId"),
                            "indentFirstLine": meta.get("indentFirstLine"),
                            "indentStart": meta.get("indentStart"),
                        }
                    ),
                    bullet=meta.get("bullet"),
                ),
            )
        )
        start_index = paragraph_end
        cursor += len(chunk)

    body = Body(content=content)
    named_styles_model = NamedStyles.model_validate(named_styles)
    lists_payload = _list_payload(paragraph_ops) or None

    document_tab = TabResource(
        tabProperties={
            "index": 0,
            "tabId": "tab-1",
            "title": "Tab 1",
        },
        documentTab=DocumentTabResource(
            body=body,
            documentStyle=document_style,
            namedStyles=named_styles_model,
            lists=lists_payload,
        ),
    )

    resource = DocumentResource(
        title=title,
        documentId=document_id,
        revisionId=str(revision_id),
        suggestionsViewMode=suggestions_view_mode,
    )
    if include_tabs_content:
        resource.tabs = [document_tab]
        return resource

    resource.body = body
    resource.documentStyle = document_style
    resource.namedStyles = named_styles_model
    resource.lists = lists_payload
    if include_tabs:
        resource.tabs = [document_tab]
    return resource


def _shift_insert(entries: list[dict], index: int, delta: int) -> list[dict]:
    shifted: list[dict] = []
    for entry in entries:
        start = entry["startIndex"]
        end = entry["endIndex"]
        updated = dict(entry)
        if start >= index:
            updated["startIndex"] = start + delta
            updated["endIndex"] = end + delta
        elif start < index < end:
            updated["endIndex"] = end + delta
        shifted.append(updated)
    return shifted


def _shift_delete(entries: list[dict], start_index: int, end_index: int) -> list[dict]:
    delta = end_index - start_index
    shifted: list[dict] = []
    for entry in entries:
        start = entry["startIndex"]
        end = entry["endIndex"]
        if end <= start_index:
            shifted.append(dict(entry))
            continue
        if start >= end_index:
            updated = dict(entry)
            updated["startIndex"] = start - delta
            updated["endIndex"] = end - delta
            shifted.append(updated)
            continue

        new_start = start
        new_end = end
        if start < start_index:
            new_end = max(start_index, end - delta)
        else:
            new_start = start_index
            new_end = max(start_index, end - delta)

        if new_end > new_start:
            updated = dict(entry)
            updated["startIndex"] = new_start
            updated["endIndex"] = new_end
            shifted.append(updated)
    return shifted


def _merge_style_spans(text: str, spans: list[dict]) -> list[dict]:
    text = normalize_body_text(text)
    if len(text) <= 1:
        return []

    by_index: list[dict[str, Any]] = []
    for index in range(1, len(text) + 1):
        style: dict[str, Any] = {}
        for span in spans:
            if span["startIndex"] <= index < span["endIndex"]:
                style.update(span.get("textStyle", {}))
        by_index.append(style)

    merged: list[dict] = []
    current_style = by_index[0]
    current_start = 1
    for idx, style in enumerate(by_index[1:], start=2):
        if style != current_style:
            if current_style:
                merged.append(
                    {
                        "startIndex": current_start,
                        "endIndex": idx,
                        "textStyle": current_style,
                    }
                )
            current_style = style
            current_start = idx
    if current_style:
        merged.append(
            {
                "startIndex": current_start,
                "endIndex": len(text) + 1,
                "textStyle": current_style,
            }
        )
    return merged


def _merge_paragraph_ops(entries: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[Any, ...]] = set()
    for entry in entries:
        key = (
            entry.get("startIndex"),
            entry.get("endIndex"),
            entry.get("namedStyleType"),
            entry.get("bulletPreset"),
            entry.get("listId"),
            dump_json_field(entry.get("paragraphStyle", {})),
        )
        if key in seen:
            continue
        deduped.append(entry)
        seen.add(key)
    return sorted(deduped, key=lambda item: (item["startIndex"], item["endIndex"]))


def _validate_index(text: str, index: int, *, allow_terminal: bool = False) -> int:
    max_insert = len(text)
    max_value = max_insert + 1 if allow_terminal else max_insert
    if index < 1 or index > max_value:
        raise HTTPException(400, {"message": f"Invalid index: {index}", "reason": "badRequest"})
    return index


def _style_fields_to_list(fields: str) -> list[str]:
    raw = [field.strip() for field in fields.split(",") if field.strip()]
    expanded = []
    for field in raw:
        expanded.append(field.split(".")[0])
    return expanded


def _merge_field_mask(
    current: dict[str, Any],
    updates: dict[str, Any],
    requested_fields: list[str],
) -> dict[str, Any]:
    next_value = dict(current)
    for field in requested_fields:
        if field in updates:
            next_value[field] = updates[field]
        else:
            next_value.pop(field, None)
    return next_value


@dataclass
class EditorState:
    text: str
    text_spans: list[dict]
    paragraph_ops: list[dict]
    replies: list[ResponseItem]


def _insert_text(state: EditorState, index: int, text: str, *, record_reply: bool = True):
    state.text = state.text[: index - 1] + text + state.text[index - 1 :]
    delta = len(text)
    state.text_spans = _shift_insert(state.text_spans, index, delta)
    state.paragraph_ops = _shift_insert(state.paragraph_ops, index, delta)
    if record_reply:
        state.replies.append(ResponseItem())


def _delete_range(state: EditorState, start_index: int, end_index: int, *, record_reply: bool = False):
    if end_index <= start_index:
        raise HTTPException(400, {"message": "Invalid delete range", "reason": "badRequest"})
    state.text = normalize_body_text(state.text[: start_index - 1] + state.text[end_index - 1 :])
    state.text_spans = _merge_style_spans(
        state.text,
        _shift_delete(state.text_spans, start_index, end_index),
    )
    state.paragraph_ops = _merge_paragraph_ops(
        _shift_delete(state.paragraph_ops, start_index, end_index)
    )
    if record_reply:
        state.replies.append(ResponseItem())


def _replace_all_text(state: EditorState, contains: str, replace: str, match_case: bool):
    haystack = state.text if match_case else state.text.lower()
    needle = contains if match_case else contains.lower()
    if not needle:
        raise HTTPException(400, {"message": "containsText.text is required", "reason": "badRequest"})

    occurrences = 0
    index = 0
    while True:
        found = haystack.find(needle, index)
        if found == -1:
            break
        start_index = found + 1
        end_index = start_index + len(contains)
        _delete_range(state, start_index, end_index, record_reply=False)
        if replace:
            _insert_text(state, start_index, replace, record_reply=False)
        occurrences += 1
        haystack = state.text if match_case else state.text.lower()
        index = found + len(replace)

    state.replies.append(ResponseItem(replaceAllText={"occurrencesChanged": occurrences}))


def _update_text_style(state: EditorState, start_index: int, end_index: int, style: dict[str, Any], fields: str):
    if end_index <= start_index:
        raise HTTPException(400, {"message": "Invalid updateTextStyle range", "reason": "badRequest"})
    requested_fields = _style_fields_to_list(fields)
    invalid = [field for field in requested_fields if field not in SUPPORTED_TEXT_STYLE_FIELDS]
    if invalid:
        raise HTTPException(
            400,
            {
                "message": f"Unsupported text style field(s): {', '.join(invalid)}",
                "reason": "badRequest",
            },
        )

    state.text_spans.append(
        {
            "startIndex": start_index,
            "endIndex": end_index,
            "textStyle": {field: style.get(field) for field in requested_fields},
        }
    )
    state.text_spans = _merge_style_spans(state.text, state.text_spans)
    state.replies.append(ResponseItem())


def _paragraph_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 1
    for chunk in normalize_body_text(text).splitlines(keepends=True):
        end = start + len(chunk)
        ranges.append((start, end))
        start = end
    return ranges


def _create_paragraph_bullets(state: EditorState, start_index: int, end_index: int, bullet_preset: str):
    list_id = f"list-{uuid.uuid4().hex[:8]}"
    entries = []
    for paragraph_start, paragraph_end in _paragraph_ranges(state.text):
        overlaps = paragraph_start < end_index and paragraph_end > start_index
        if overlaps:
            entries.append(
                {
                    "startIndex": paragraph_start,
                    "endIndex": paragraph_end,
                    "bulletPreset": bullet_preset,
                    "listId": list_id,
                }
            )
    state.paragraph_ops = _merge_paragraph_ops(state.paragraph_ops + entries)
    state.replies.append(ResponseItem())


def _update_paragraph_style(
    state: EditorState,
    start_index: int,
    end_index: int,
    style: dict[str, Any],
    fields: str,
):
    if end_index <= start_index:
        raise HTTPException(400, {"message": "Invalid updateParagraphStyle range", "reason": "badRequest"})
    requested_fields = _style_fields_to_list(fields)
    invalid = [field for field in requested_fields if field not in SUPPORTED_PARAGRAPH_STYLE_FIELDS]
    if invalid:
        raise HTTPException(
            400,
            {
                "message": f"Unsupported paragraph style field(s): {', '.join(invalid)}",
                "reason": "badRequest",
            },
        )

    entries = []
    for paragraph_start, paragraph_end in _paragraph_ranges(state.text):
        overlaps = paragraph_start < end_index and paragraph_end > start_index
        if not overlaps:
            continue
        existing = next(
            (
                entry for entry in state.paragraph_ops
                if entry["startIndex"] == paragraph_start and entry["endIndex"] == paragraph_end
            ),
            {},
        )
        next_style = _merge_field_mask(existing.get("paragraphStyle", {}), style, requested_fields)
        entry = {
            "startIndex": paragraph_start,
            "endIndex": paragraph_end,
            "paragraphStyle": next_style,
        }
        if next_style.get("namedStyleType"):
            entry["namedStyleType"] = next_style["namedStyleType"]
        if existing.get("bulletPreset"):
            entry["bulletPreset"] = existing.get("bulletPreset")
            entry["listId"] = existing.get("listId")
        entries.append(entry)

    untouched = [
        entry for entry in state.paragraph_ops
        if not any(
            entry["startIndex"] == candidate["startIndex"] and entry["endIndex"] == candidate["endIndex"]
            for candidate in entries
        )
    ]
    state.paragraph_ops = _merge_paragraph_ops(untouched + entries)
    state.replies.append(ResponseItem())


def _delete_paragraph_bullets(state: EditorState, start_index: int, end_index: int):
    next_entries = []
    for entry in state.paragraph_ops:
        overlaps = entry["startIndex"] < end_index and entry["endIndex"] > start_index
        if overlaps and entry.get("bulletPreset"):
            updated = dict(entry)
            updated.pop("bulletPreset", None)
            updated.pop("listId", None)
            next_entries.append(updated)
            continue
        next_entries.append(entry)
    state.paragraph_ops = _merge_paragraph_ops(next_entries)
    state.replies.append(ResponseItem())


def _update_document_style(document_style_json: str, style: dict[str, Any], fields: str) -> str:
    current = load_json_field(document_style_json, default_document_style())
    requested_fields = _style_fields_to_list(fields)
    invalid = [field for field in requested_fields if field not in SUPPORTED_DOCUMENT_STYLE_FIELDS]
    if invalid:
        raise HTTPException(
            400,
            {
                "message": f"Unsupported document style field(s): {', '.join(invalid)}",
                "reason": "badRequest",
            },
        )
    next_style = _merge_field_mask(current, style, requested_fields)
    return dump_json_field(next_style)


def apply_batch_requests(
    *,
    body_text: str,
    text_style_spans_json: str,
    paragraph_style_json: str,
    document_style_json: str,
    requests: list[dict[str, Any]],
) -> tuple[str, str, str, str, list[ResponseItem]]:
    state = EditorState(
        text=normalize_body_text(body_text),
        text_spans=load_json_field(text_style_spans_json, []),
        paragraph_ops=load_json_field(paragraph_style_json, []),
        replies=[],
    )
    next_document_style_json = document_style_json

    for idx, request in enumerate(requests):
        try:
            if request.get("insertText") is not None:
                payload = request["insertText"]
                if payload.get("location") is not None:
                    index = _validate_index(state.text, int(payload["location"]["index"]))
                elif payload.get("endOfSegmentLocation") is not None:
                    index = max(1, len(state.text))
                else:
                    raise HTTPException(400, {"message": "insertText requires a location", "reason": "badRequest"})
                _insert_text(state, index, payload.get("text", ""))
                continue

            if request.get("replaceAllText") is not None:
                payload = request["replaceAllText"]
                contains = payload.get("containsText", {})
                _replace_all_text(
                    state,
                    contains.get("text", ""),
                    payload.get("replaceText", ""),
                    bool(contains.get("matchCase", True)),
                )
                continue

            if request.get("deleteContentRange") is not None:
                payload = request["deleteContentRange"]["range"]
                start_index = _validate_index(state.text, int(payload["startIndex"]))
                end_index = _validate_index(state.text, int(payload["endIndex"]), allow_terminal=True)
                _delete_range(state, start_index, end_index, record_reply=True)
                continue

            if request.get("updateTextStyle") is not None:
                payload = request["updateTextStyle"]
                span = payload["range"]
                start_index = _validate_index(state.text, int(span["startIndex"]))
                end_index = _validate_index(state.text, int(span["endIndex"]), allow_terminal=True)
                _update_text_style(
                    state,
                    start_index,
                    end_index,
                    payload.get("textStyle", {}),
                    payload.get("fields", ""),
                )
                continue

            if request.get("updateParagraphStyle") is not None:
                payload = request["updateParagraphStyle"]
                span = payload["range"]
                start_index = _validate_index(state.text, int(span["startIndex"]))
                end_index = _validate_index(state.text, int(span["endIndex"]), allow_terminal=True)
                _update_paragraph_style(
                    state,
                    start_index,
                    end_index,
                    payload.get("paragraphStyle", {}),
                    payload.get("fields", ""),
                )
                continue

            if request.get("createParagraphBullets") is not None:
                payload = request["createParagraphBullets"]
                span = payload["range"]
                start_index = _validate_index(state.text, int(span["startIndex"]))
                end_index = _validate_index(state.text, int(span["endIndex"]), allow_terminal=True)
                _create_paragraph_bullets(
                    state,
                    start_index,
                    end_index,
                    payload.get("bulletPreset", "BULLET_DISC_CIRCLE_SQUARE"),
                )
                continue

            if request.get("deleteParagraphBullets") is not None:
                payload = request["deleteParagraphBullets"]
                span = payload["range"]
                start_index = _validate_index(state.text, int(span["startIndex"]))
                end_index = _validate_index(state.text, int(span["endIndex"]), allow_terminal=True)
                _delete_paragraph_bullets(state, start_index, end_index)
                continue

            if request.get("updateDocumentStyle") is not None:
                payload = request["updateDocumentStyle"]
                next_document_style_json = _update_document_style(
                    next_document_style_json,
                    payload.get("documentStyle", {}),
                    payload.get("fields", ""),
                )
                state.replies.append(ResponseItem())
                continue

            raise HTTPException(
                400,
                {"message": "Unsupported request type", "reason": "badRequest"},
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            message = detail.get("message", "Bad request")
            reason = detail.get("reason", "badRequest")
            raise HTTPException(
                400,
                {
                    "message": f"Request {idx} failed: {message}",
                    "reason": reason,
                },
            ) from exc

    return (
        normalize_body_text(state.text),
        dump_json_field(state.text_spans),
        dump_json_field(state.paragraph_ops),
        next_document_style_json,
        state.replies,
    )
