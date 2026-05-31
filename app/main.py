"""
Store Intelligence API — FastAPI entrypoint.
Structured logging on every request: trace_id, store_id, endpoint, latency_ms, status_code.
Graceful degradation: DB errors → 503, not stack traces.
"""
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import init_db, load_pos_data
from .models import (
    EventBatch, IngestResponse,
    StoreMetrics, StoreFunnel, StoreHeatmap, StoreAnomalies, HealthResponse
)
from .ingestion import ingest_events_batch
from .metrics   import compute_metrics
from .funnel    import compute_funnel
from .anomalies import compute_anomalies
from .health    import get_health
from . import heatmap as heatmap_module

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
)
logger = logging.getLogger(__name__)

VALID_STORES = {"STORE_BLR_002"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await load_pos_data()
    logger.info(json.dumps({"event": "startup", "status": "ready"}))
    yield
    logger.info(json.dumps({"event": "shutdown"}))


app = FastAPI(
    title="Store Intelligence API",
    description="AI-powered retail analytics from CCTV footage",
    version="1.0.0",
    lifespan=lifespan,
)

# Serve live dashboard at /dashboard
try:
    app.mount("/dashboard", StaticFiles(directory="static", html=True), name="dashboard")
except Exception:
    pass  # static dir may not exist in all environments

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def structured_log(request: Request, call_next):
    trace_id  = uuid.uuid4().hex[:8]
    store_id  = request.path_params.get("store_id", "")
    start     = time.perf_counter()
    response  = await call_next(request)
    latency   = int((time.perf_counter() - start) * 1000)

    log = {
        "trace_id":   trace_id,
        "store_id":   store_id,
        "endpoint":   request.url.path,
        "method":     request.method,
        "status_code": response.status_code,
        "latency_ms": latency,
    }
    logger.info(json.dumps(log))
    response.headers["X-Trace-Id"] = trace_id
    return response


def _check_store(store_id: str):
    if store_id not in VALID_STORES:
        raise HTTPException(
            status_code=404,
            detail={"error": "store_not_found", "store_id": store_id,
                    "valid_stores": list(VALID_STORES)}
        )


def _db_error_response(exc: Exception) -> JSONResponse:
    logger.error(json.dumps({"event": "db_error", "error": str(exc)}))
    return JSONResponse(
        status_code=503,
        content={"error": "service_unavailable",
                 "message": "Database temporarily unavailable. Retry shortly."}
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/events/ingest", response_model=IngestResponse, status_code=200)
async def ingest_events(payload: EventBatch, request: Request):
    """
    Ingest a batch of up to 500 events.
    Idempotent by event_id. Partial success on malformed events.
    """
    try:
        result = await ingest_events_batch(payload.events)
        # Add event_count to log (picked up by middleware via response)
        return result
    except Exception as exc:
        return _db_error_response(exc)


@app.get("/stores/{store_id}/metrics", response_model=StoreMetrics)
async def get_metrics(store_id: str):
    """Real-time store metrics: visitors, conversion rate, dwell, queue, abandonment."""
    _check_store(store_id)
    try:
        return await compute_metrics(store_id)
    except Exception as exc:
        return _db_error_response(exc)


@app.get("/stores/{store_id}/funnel", response_model=StoreFunnel)
async def get_funnel(store_id: str):
    """Conversion funnel: Entry → Zone Visit → Billing Queue → Purchase."""
    _check_store(store_id)
    try:
        return await compute_funnel(store_id)
    except Exception as exc:
        return _db_error_response(exc)


@app.get("/stores/{store_id}/heatmap", response_model=StoreHeatmap)
async def get_heatmap(store_id: str):
    """Zone visit frequency and avg dwell, normalised 0–100."""
    _check_store(store_id)
    try:
        return await heatmap_module.compute_heatmap(store_id)
    except Exception as exc:
        return _db_error_response(exc)


@app.get("/stores/{store_id}/anomalies", response_model=StoreAnomalies)
async def get_anomalies(store_id: str):
    """Active anomalies: queue spike, conversion drop, dead zone, stale feed."""
    _check_store(store_id)
    try:
        return await compute_anomalies(store_id)
    except Exception as exc:
        return _db_error_response(exc)


@app.get("/health", response_model=HealthResponse)
async def health():
    """Service status and per-camera feed lag. STALE_FEED if >10 min since last event."""
    return await get_health()
