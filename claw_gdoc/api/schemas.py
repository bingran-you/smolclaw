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
    model_config = {"exclude_none": True, "extra": "allow"}

    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    strikethrough: bool | None = None
    foregroundColor: dict[str, Any] | None = None
    backgroundColor: dict[str, Any] | None = None
    fontSize: dict[str, Any] | None = None
    weightedFontFamily: dict[str, Any] | None = None
    baselineOffset: str | None = None
    smallCaps: bool | None = None
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


class UpdateParagraphStyleRequest(BaseModel):
    range: Range
    paragraphStyle: dict[str, Any] = Field(default_factory=dict)
    fields: str = ""


class CreateParagraphBulletsRequest(BaseModel):
    range: Range
    bulletPreset: str = "BULLET_DISC_CIRCLE_SQUARE"


class DeleteParagraphBulletsRequest(BaseModel):
    range: Range


class UpdateDocumentStyleRequest(BaseModel):
    documentStyle: dict[str, Any] = Field(default_factory=dict)
    fields: str = ""


class CreateNamedRangeRequest(BaseModel):
    name: str
    range: Range


class DeleteNamedRangeRequest(BaseModel):
    namedRangeId: str


class ReplaceNamedRangeContentRequest(BaseModel):
    namedRangeId: str
    text: str = ""


class RequestItem(BaseModel):
    insertText: InsertTextRequest | None = None
    replaceAllText: ReplaceAllTextRequest | None = None
    deleteContentRange: DeleteContentRangeRequest | None = None
    updateTextStyle: UpdateTextStyleRequest | None = None
    updateParagraphStyle: UpdateParagraphStyleRequest | None = None
    createParagraphBullets: CreateParagraphBulletsRequest | None = None
    deleteParagraphBullets: DeleteParagraphBulletsRequest | None = None
    updateDocumentStyle: UpdateDocumentStyleRequest | None = None
    createNamedRange: CreateNamedRangeRequest | None = None
    deleteNamedRange: DeleteNamedRangeRequest | None = None
    replaceNamedRangeContent: ReplaceNamedRangeContentRequest | None = None


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


class CreateNamedRangeResponse(BaseModel):
    namedRangeId: str


class ResponseItem(BaseModel):
    model_config = {"exclude_none": True}

    insertText: InsertTextResponse | None = None
    replaceAllText: ReplaceAllTextResponse | None = None
    updateTextStyle: UpdateTextStyleResponse | None = None
    createParagraphBullets: CreateParagraphBulletsResponse | None = None
    createNamedRange: CreateNamedRangeResponse | None = None


class TextRun(BaseModel):
    model_config = {"exclude_none": True}

    content: str
    textStyle: TextStyle = Field(default_factory=TextStyle)


class ParagraphElement(BaseModel):
    startIndex: int
    endIndex: int
    textRun: TextRun


class Bullet(BaseModel):
    model_config = {"exclude_none": True, "extra": "allow"}

    listId: str
    nestingLevel: int = 0
    textStyle: TextStyle = Field(default_factory=TextStyle)


class ParagraphStyle(BaseModel):
    model_config = {"exclude_none": True, "extra": "allow"}

    direction: str = "LEFT_TO_RIGHT"
    namedStyleType: str = "NORMAL_TEXT"
    headingId: str | None = None
    indentFirstLine: dict[str, Any] | None = None
    indentStart: dict[str, Any] | None = None


class Paragraph(BaseModel):
    model_config = {"exclude_none": True}

    elements: list[ParagraphElement]
    paragraphStyle: ParagraphStyle = Field(default_factory=ParagraphStyle)
    bullet: Bullet | None = None


class SectionBreak(BaseModel):
    sectionStyle: dict[str, Any] = Field(default_factory=dict)


class StructuralElement(BaseModel):
    model_config = {"exclude_none": True}

    startIndex: int | None = None
    endIndex: int
    paragraph: Paragraph | None = None
    sectionBreak: SectionBreak | None = None


class Body(BaseModel):
    content: list[StructuralElement]


class ListProperties(BaseModel):
    nestingLevels: list[dict[str, Any]] = Field(default_factory=list)


class NamedStyles(BaseModel):
    styles: list[dict[str, Any]] = Field(default_factory=list)


class NamedRangeResource(BaseModel):
    namedRangeId: str
    name: str
    ranges: list[Range] = Field(default_factory=list)


class DocumentTabResource(BaseModel):
    model_config = {"exclude_none": True}

    body: Body
    documentStyle: dict[str, Any] = Field(default_factory=dict)
    namedStyles: NamedStyles = Field(default_factory=NamedStyles)
    lists: dict[str, dict[str, Any]] | None = None


class TabResource(BaseModel):
    model_config = {"exclude_none": True}

    tabProperties: dict[str, Any] = Field(default_factory=dict)
    documentTab: DocumentTabResource


class DocumentResource(BaseModel):
    model_config = {"exclude_none": True}

    title: str
    documentId: str
    revisionId: str
    body: Body | None = None
    documentStyle: dict[str, Any] | None = None
    namedStyles: NamedStyles | None = None
    namedRanges: dict[str, list[NamedRangeResource]] | None = None
    lists: dict[str, dict[str, Any]] | None = None
    suggestionsViewMode: str = "SUGGESTIONS_INLINE"
    tabs: list[TabResource] | None = None


class DocumentCreateRequest(BaseModel):
    model_config = {"extra": "ignore"}

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
    name: str | None = None
    mimeType: str | None = None
    description: str | None = None
    createdTime: str | None = None
    modifiedTime: str | None = None
    trashed: bool | None = None
    webViewLink: str | None = None
    iconLink: str | None = None
    exportLinks: dict[str, str] | None = None
    ownedByMe: bool | None = None


class DriveFileList(BaseModel):
    kind: Literal["drive#fileList"] = "drive#fileList"
    files: list[DriveFileResource] = Field(default_factory=list)
    nextPageToken: str | None = None


class DrivePermissionResource(BaseModel):
    model_config = {"exclude_none": True}

    kind: Literal["drive#permission"] = "drive#permission"
    id: str
    type: str = "user"
    role: str
    emailAddress: str | None = None
    displayName: str | None = None
    deleted: bool | None = None
    allowFileDiscovery: bool | None = None


class DrivePermissionList(BaseModel):
    kind: Literal["drive#permissionList"] = "drive#permissionList"
    permissions: list[DrivePermissionResource] = Field(default_factory=list)


class DrivePermissionCreateRequest(BaseModel):
    model_config = {"extra": "ignore"}

    type: str = "user"
    role: str = "reader"
    emailAddress: str
    allowFileDiscovery: bool = False


class DrivePermissionUpdateRequest(BaseModel):
    model_config = {"extra": "ignore"}

    role: str | None = None
    allowFileDiscovery: bool | None = None


class DriveStartPageToken(BaseModel):
    startPageToken: str


class DriveChangeResource(BaseModel):
    model_config = {"exclude_none": True}

    kind: Literal["drive#change"] = "drive#change"
    changeType: str
    fileId: str
    removed: bool
    time: str
    file: DriveFileResource | None = None


class DriveChangeList(BaseModel):
    kind: Literal["drive#changeList"] = "drive#changeList"
    changes: list[DriveChangeResource] = Field(default_factory=list)
    nextPageToken: str | None = None
    newStartPageToken: str | None = None
