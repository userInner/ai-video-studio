from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateProjectRequest(BaseModel):
    input: str = Field(min_length=2, max_length=1000)
    mode: Literal["title", "idea", "inspire"] = "title"


class ProjectCreated(BaseModel):
    project_id: str
    run_id: str


class ContractBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceContract(ContractBase):
    title: str
    url: str
    publisher: str
    published_at: str
    credibility: Literal["primary", "strong", "supporting"]
    summary: str

    @field_validator("url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("source URL must use HTTP(S)")
        return value


class TopicOptionContract(ContractBase):
    label: str
    title: str
    hook: str
    insight: str
    emotion: str
    audience: str
    narrative: list[str] = Field(min_length=3, max_length=6)
    risk: str


class DiscoveryContract(ContractBase):
    corrected_title: str
    fact_note: str
    sources: list[SourceContract] = Field(min_length=2, max_length=8)
    options: list[TopicOptionContract] = Field(min_length=3, max_length=3)


class AngleDiscoveryContract(ContractBase):
    corrected_title: str
    fact_note: str
    options: list[TopicOptionContract] = Field(min_length=3, max_length=3)


class TopicOptionView(TopicOptionContract):
    id: str
    rank: int


class SourceView(BaseModel):
    id: str
    title: str
    url: str
    publisher: str
    published_at: str
    credibility: str
    summary: str


class RunView(BaseModel):
    id: str
    status: str
    step: str
    progress: int
    error: str | None


class ProductionCardView(BaseModel):
    id: str
    version: int
    status: str
    title: str
    promise: str
    audience: str
    duration_seconds: int
    visual_style: str
    tone: str
    structure: list[str]


class ProjectView(BaseModel):
    id: str
    title: str
    brief: str
    stage: str
    input_mode: str
    selected_topic_id: str | None
    created_at: datetime
    sources: list[SourceView]
    topic_options: list[TopicOptionView]
    latest_run: RunView | None
    production_card: ProductionCardView | None


class SelectTopicRequest(BaseModel):
    topic_option_id: str
    duration_seconds: int = Field(default=300, ge=180, le=600)


class UpdateProductionCardRequest(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=240)
    duration_seconds: int | None = Field(default=None, ge=180, le=600)


class DataPointContract(ContractBase):
    label: str = Field(min_length=1, max_length=40)
    value: float
    display_value: str = Field(min_length=1, max_length=24)
    unit: str = Field(default="", max_length=12)
    source_url: str = ""


class RelationshipContract(ContractBase):
    source: str = Field(min_length=1, max_length=40)
    target: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=40)


class ScriptSectionContract(ContractBase):
    section_type: Literal["hook", "context", "evidence", "analysis", "turn", "takeaway", "closing"]
    title: str
    purpose: str
    narration: str
    visual_direction: str
    claim_source_urls: list[str]
    entities: list[str] = Field(default_factory=list, max_length=10)
    relationships: list[RelationshipContract] = Field(default_factory=list, max_length=8)
    data_points: list[DataPointContract] = Field(default_factory=list, max_length=8)
    estimated_seconds: int = Field(ge=10, le=120)


class ScriptContract(ContractBase):
    title: str
    opening_hook: str
    thesis: str
    audience_takeaway: str
    estimated_duration_seconds: int = Field(ge=180, le=600)
    sections: list[ScriptSectionContract] = Field(min_length=5, max_length=20)
    closing: str
