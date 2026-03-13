"""Pydantic schemas mirroring the mock Google Docs and Drive APIs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Range(BaseModel):
    startIndex: int
    endIndex: int


class Location(BaseModel):
    index: int


class EndOfSegmentLocation(BaseModel):
    segmentId: str | None = None


class OptionalColor(BaseModel):
    color: dict[str, Any] | None = None


class Link(BaseModel):
    url: str | None = None


class TextStyle(BaseModel):
    model_config = {"exclude_none": True}

    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    link: Link | None = None


class InsertTextRequest(BaseModel):
    location: Location | None = None
    endOfSegmentLocation: EndOfSegmentLocation | None = None
    text: str = ""


class ContainsText(BaseModel):
    text: str
    matchCase: bool = True


class ReplaceAllTextRequest(BaseModel):
    containsText: ContainsText
    replaceText: str = ""


class DeleteContentRangeRequest(BaseModel):
    range: Range


class UpdateTextStyleRequest(BaseModel):
    range: Range
    textStyle: TextStyle = Field(default_factory=TextStyle)
    fields: str = ""


class CreateParagraphBulletsRequest(BaseModel):
    range: Range
    bulletPreset: str = "BULLET_DISC_CIRCLE_SQUARE"


class RequestItem(BaseModel):
    insertText: InsertTextRequest | None = None
    replaceAllText: ReplaceAllTextRequest | None = None
    deleteContentRange: DeleteContentRangeRequest | None = None
    updateTextStyle: UpdateTextStyleRequest | None = None
    createParagraphBullets: CreateParagraphBulletsRequest | None = None


class WriteControl(BaseModel):
    requiredRevisionId: str | None = None


class BatchUpdateRequest(BaseModel):
    requests: list[RequestItem] = Field(default_factory=list)
    writeControl: WriteControl | None = None


class InsertTextResponse(BaseModel):
    insertedText: str


class ReplaceAllTextResponse(BaseModel):
    occurrencesChanged: int


class UpdateTextStyleResponse(BaseModel):
    applied: bool = True


class CreateParagraphBulletsResponse(BaseModel):
    applied: bool = True


class ResponseItem(BaseModel):
    model_config = {"exclude_none": True}

    insertText: InsertTextResponse | None = None
    replaceAllText: ReplaceAllTextResponse | None = None
    updateTextStyle: UpdateTextStyleResponse | None = None
    createParagraphBullets: CreateParagraphBulletsResponse | None = None


class TextRun(BaseModel):
    model_config = {"exclude_none": True}

    content: str
    textStyle: TextStyle | None = None


class ParagraphElement(BaseModel):
    startIndex: int
    endIndex: int
    textRun: TextRun


class Bullet(BaseModel):
    model_config = {"exclude_none": True}

    listId: str
    nestingLevel: int = 0


class ParagraphStyle(BaseModel):
    namedStyleType: str = "NORMAL_TEXT"


class Paragraph(BaseModel):
    model_config = {"exclude_none": True}

    elements: list[ParagraphElement]
    paragraphStyle: ParagraphStyle = Field(default_factory=ParagraphStyle)
    bullet: Bullet | None = None


class SectionBreak(BaseModel):
    sectionStyle: dict[str, Any] = Field(default_factory=dict)


class StructuralElement(BaseModel):
    model_config = {"exclude_none": True}

    startIndex: int
    endIndex: int
    paragraph: Paragraph | None = None
    sectionBreak: SectionBreak | None = None


class Body(BaseModel):
    content: list[StructuralElement]


class ListProperties(BaseModel):
    nestingLevels: list[dict[str, Any]] = Field(default_factory=list)


class NamedStyles(BaseModel):
    styles: list[dict[str, Any]] = Field(default_factory=list)


class DocumentResource(BaseModel):
    model_config = {"exclude_none": True}

    title: str
    documentId: str
    revisionId: str
    body: Body
    documentStyle: dict[str, Any] = Field(default_factory=dict)
    namedStyles: NamedStyles = Field(default_factory=NamedStyles)
    lists: dict[str, dict[str, Any]] = Field(default_factory=dict)


class DocumentCreateRequest(BaseModel):
    title: str = ""


class BatchUpdateResponse(BaseModel):
    documentId: str
    replies: list[ResponseItem]
    writeControl: WriteControl


class Profile(BaseModel):
    emailAddress: str
    displayName: str
    documentsTotal: int
    historyId: str


class DriveFileResource(BaseModel):
    model_config = {"exclude_none": True}

    kind: Literal["drive#file"] = "drive#file"
    id: str
    name: str
    mimeType: str
    createdTime: str
    modifiedTime: str
    trashed: bool = False
    webViewLink: str
    iconLink: str
    exportLinks: dict[str, str] = Field(default_factory=dict)


class DriveFileList(BaseModel):
    kind: Literal["drive#fileList"] = "drive#fileList"
    files: list[DriveFileResource]
    nextPageToken: str | None = None
