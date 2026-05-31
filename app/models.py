"""
Pydantic models for the Store Intelligence API.
Schema matches the problem statement event spec exactly.
"""
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Literal
from datetime import datetime
import uuid

# ---------------------------------------------------------------------------
# Event schema (inbound — from detection pipeline)
# ---------------------------------------------------------------------------

class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 0


EventType = Literal[
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
    "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
]


class StoreEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: str
    zone_id: Optional[str] = None
    dwell_ms: int = Field(default=0, ge=0)
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid ISO-8601 timestamp: {v}")
        return v

    @model_validator(mode="after")
    def validate_zone_rules(self) -> "StoreEvent":
        if self.event_type in ("ENTRY", "EXIT", "REENTRY"):
            if self.zone_id is not None:
                raise ValueError(f"zone_id must be null for {self.event_type} events")
        elif self.event_type in ("ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
                                  "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"):
            if not self.zone_id:
                raise ValueError(f"zone_id is required for {self.event_type} events")
        return self


class EventBatch(BaseModel):
    events: List[StoreEvent]

    @field_validator("events")
    @classmethod
    def validate_batch_size(cls, v: list) -> list:
        if len(v) > 500:
            raise ValueError(f"Batch too large: {len(v)} (max 500)")
        return v


# Ingest response
class IngestError(BaseModel):
    event_id: str
    reason: str

class IngestResponse(BaseModel):
    ingested: int
    duplicates: int
    errors: List[IngestError] = []


# Metrics
class ZoneDwellMetric(BaseModel):
    zone_id: str
    avg_dwell_ms: float
    visit_count: int

class StoreMetrics(BaseModel):
    store_id: str
    as_of: str
    unique_visitors: int
    entry_camera_events: Optional[int] = None
    conversion_rate: float
    estimated_daily_conversion_rate: Optional[float] = None
    estimated_daily_conversion_basis: Optional[str] = None
    avg_dwell_per_zone: List[ZoneDwellMetric]
    queue_depth: int
    abandonment_rate: float
    data_window_minutes: int
    data_confidence: Optional[str] = None


# Funnel
class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off_pct: float
    data_confidence: Optional[str] = None

class StoreFunnel(BaseModel):
    store_id: str
    as_of: str
    stages: List[FunnelStage]
    note: Optional[str] = None


# Heatmap
class ZoneHeatmap(BaseModel):
    zone_id: str
    zone_name: str
    visit_frequency: int
    avg_dwell_ms: float
    normalized_score: float
    data_confidence: bool = True

class StoreHeatmap(BaseModel):
    store_id: str
    as_of: str
    zones: List[ZoneHeatmap]


# Anomalies
Severity = Literal["INFO", "WARN", "CRITICAL"]

class Anomaly(BaseModel):
    anomaly_type: str
    severity: Severity
    description: str
    suggested_action: str
    detected_at: str
    zone_id: Optional[str] = None

class StoreAnomalies(BaseModel):
    store_id: str
    as_of: str
    anomalies: List[Anomaly]


# Health
class CameraFeedStatus(BaseModel):
    camera_id: str
    last_event_at: Optional[str]
    lag_minutes: Optional[float]
    status: Literal["OK", "STALE", "NO_EVENTS"]

class StoreHealth(BaseModel):
    store_id: str
    camera_feeds: List[CameraFeedStatus]

class HealthResponse(BaseModel):
    status: Literal["OK", "DEGRADED", "DOWN"]
    as_of: str
    stores: List[StoreHealth]
    warnings: List[str] = []