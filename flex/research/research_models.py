from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=3, max_length=500)
    category: str = Field(default="general", pattern="^(general|education|product)$")
    budget_amount: float | None = Field(
        default=None,
        gt=0,
        le=10_000_000,
        allow_inf_nan=False,
    )
    budget_currency: str | None = Field(default=None, min_length=3, max_length=8)
    market: str | None = Field(default=None, max_length=100)


class OpenRecommendedPageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_id: int = Field(ge=1)
    browser: Literal["chrome", "edge", "chromium"] | None = None


class ResearchHistoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=10, ge=1, le=50)


class ClearResearchHistoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_delete: bool = True
