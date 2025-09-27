from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


# ---------- Input Schemas ----------

Estimate = Literal["S", "M", "L"]


class IssueSpec(BaseModel):
    title: str
    summary: str
    epic_label: Optional[str] = None
    epic_id: Optional[str] = None  # Back-compat for provided sample; mapped to epic_label internally.
    labels: List[str] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list)
    estimate: Optional[Estimate] = None

    @field_validator("title")
    @classmethod
    def title_trim_nonempty(cls, v: str) -> str:
        v2 = v.strip()
        if not v2:
            raise ValueError("title must be non-empty")
        return v2


class IssueSpecList(BaseModel):
    owner: str
    repo: str
    project_title: str
    dry_run: bool = False
    items: List[IssueSpec]

    @field_validator("owner", "repo", "project_title")
    @classmethod
    def nonempty(cls, v: str) -> str:
        v2 = v.strip()
        if not v2:
            raise ValueError("must be non-empty")
        return v2


# ---------- Output Schemas ----------

class IssueRecord(BaseModel):
    number: int
    url: HttpUrl
    title: str
    labels: List[str]
    state: Literal["open", "closed"]
    created_at: datetime
    updated_at: datetime
    project_item_id: Optional[str] = None
    status_option: Optional[str] = None


class UpdatedChange(BaseModel):
    title: str
    number: int
    url: HttpUrl
    changes: List[Literal["labels", "project_item", "status"]]


class ReportItem(BaseModel):
    title: str
    number: int
    url: HttpUrl


class ErrorItem(BaseModel):
    title: str
    reason: str
    detail: str


class Metrics(BaseModel):
    total: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0
    duration_ms: int = 0


class BaseReport(BaseModel):
    owner: str
    repo: str
    project_title: str
    project_url: Optional[str] = None
    created: List[ReportItem] = Field(default_factory=list)
    updated: List[UpdatedChange] = Field(default_factory=list)
    unchanged: List[ReportItem] = Field(default_factory=list)
    errors: List[ErrorItem] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)


ValidationReport = BaseReport
SyncReport = BaseReport


class IssuesPage(BaseModel):
    page: int
    per_page: int
    has_next: bool
    next_page: Optional[int] = None


class IssuesListResponse(BaseModel):
    owner: str
    repo: str
    project_title: Optional[str] = None
    etag: Optional[str] = None
    pagination: IssuesPage
    items: List[IssueRecord]
