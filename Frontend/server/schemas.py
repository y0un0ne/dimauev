from __future__ import annotations

from datetime import date as Date
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class DateScope(BaseModel):
    date: Optional[Date] = None
    start_date: Optional[Date] = None
    end_date: Optional[Date] = None

    @model_validator(mode="after")
    def validate_scope(self) -> "DateScope":
        if self.date and (self.start_date or self.end_date):
            raise ValueError("Use either date or start_date/end_date")
        if not self.date and not self.start_date:
            raise ValueError("date or start_date is required")
        if self.start_date and not self.end_date:
            raise ValueError("end_date is required with start_date")
        if self.end_date and not self.start_date:
            raise ValueError("start_date is required with end_date")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on/after start_date")
        return self


class ExtractRequest(DateScope):
    scan_mode: Literal["normal", "rescan", "retry_missing"] = "normal"
    continue_on_error: bool = False
    min_detail_coverage: float = Field(default=0.90, gt=0, le=1)
    max_missing_details: int = Field(default=25, ge=-1)


class EvaluateRequest(DateScope):
    cloud_data_consent: Literal[True]
    mode: Literal["normal", "retry_errors", "force"] = "normal"
    delay: float = Field(default=4.2, ge=0, le=300)
    limit: int = Field(default=0, ge=0, le=100000)
    request_timeout: int = Field(default=90, ge=10, le=600)
    max_images: int = Field(default=12, ge=0, le=100)
    thinking_level: Literal["low", "medium", "high"] = "low"
    text_models: Optional[str] = Field(default=None, max_length=2000)
    vision_models: Optional[str] = Field(default=None, max_length=2000)


class ReportRequest(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class CredentialRequest(BaseModel):
    value: str = Field(min_length=1, max_length=1280)


class GeminiAccountRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    email: str = Field(default="", max_length=254)
    api_key: str = Field(min_length=1, max_length=1280)
