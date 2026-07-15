"""
Schemas for Device Telemetry Monitoring (Section 3.15).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class TelemetryEnrollmentRequest(BaseModel):
    entity_id: str
    power_tracking: bool = True
    availability_tracking: bool = True
    usage_tracking: bool = True
    offline_alert_threshold_minutes: int = 30
    group_id: str | None = None
    power_attribute: str | None = None


class TelemetrySnapshot(BaseModel):
    entity_id: str
    power_w: float | None = None
    is_available: bool = True
    state: str | None = None
    source: str = "poll"


class TelemetryQueryRequest(BaseModel):
    entity_id: str
    hours: int = 24
    dimension: str | None = None  # "power", "availability", "usage", or None for all


class TelemetryInsightRequest(BaseModel):
    entity_id: str | None = None
    hours: int = 168  # 7 days default
    force_analysis: bool = False


class TelemetryEnrollment(BaseModel):
    entity_id: str
    power_tracking: bool
    availability_tracking: bool
    usage_tracking: bool
    offline_alert_threshold_minutes: int
    group_id: str | None = None
    enrolled_at: str


class TelemetryDataPoint(BaseModel):
    recorded_at: float
    power_w: float | None = None
    is_available: bool
    state: str | None = None


class TelemetrySummary(BaseModel):
    entity_id: str
    current_power_w: float | None = None
    peak_power_w: float | None = None
    avg_power_w: float | None = None
    availability_pct: float = 100.0
    total_activations: int = 0
    last_outage_at: str | None = None
    last_outage_duration_minutes: float | None = None
    data_points: list[TelemetryDataPoint] = Field(default_factory=list)


class LLMInsight(BaseModel):
    entity_id: str
    insight_text: str
    confidence: float = 0.0
    generated_at: str
    pattern_type: str = "usage"
