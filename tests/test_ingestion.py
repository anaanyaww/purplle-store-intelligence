# PROMPT: "Write pytest tests for a FastAPI events/ingest endpoint: idempotency by event_id,
# partial batch failure, batch size limit of 500, staff exclusion from metrics,
# zero-purchase store returns 0.0 not null, 404 for unknown store, REENTRY session logic."
# CHANGES MADE: Moved fixtures to conftest, added assertion messages, added REENTRY uniqueness check.

import pytest
from tests.conftest import make_event


@pytest.mark.asyncio
async def test_ingest_single_event(client):
    r = await client.post("/events/ingest", json={"events": [make_event()]})
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] == 1
    assert body["duplicates"] == 0
    assert body["errors"] == []


@pytest.mark.asyncio
async def test_idempotency(client):
    evt = make_event()
    await client.post("/events/ingest", json={"events": [evt]})
    r = await client.post("/events/ingest", json={"events": [evt]})
    body = r.json()
    assert body["ingested"] == 0, "Re-ingesting same event_id must not increase ingested count"
    assert body["duplicates"] == 1, "Duplicate must be counted"


@pytest.mark.asyncio
async def test_batch_size_limit(client):
    events = [make_event({"event_id": f"evt-{i}"}) for i in range(501)]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 422, "Batches over 500 events must be rejected with 422"


@pytest.mark.asyncio
async def test_empty_batch(client):
    r = await client.post("/events/ingest", json={"events": []})
    assert r.status_code == 200
    assert r.json()["ingested"] == 0


@pytest.mark.asyncio
async def test_staff_excluded_from_unique_visitors(client):
    staff = make_event({"event_id": "staff-001", "is_staff": True, "visitor_id": "VIS_staff"})
    await client.post("/events/ingest", json={"events": [staff]})
    r = await client.get("/stores/STORE_BLR_002/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 0, "Staff must not count as visitors"


@pytest.mark.asyncio
async def test_zero_purchases_returns_zero_not_null(client):
    await client.post("/events/ingest", json={"events": [make_event()]})
    r = await client.get("/stores/STORE_BLR_002/metrics")
    assert r.status_code == 200
    assert r.json()["conversion_rate"] == 0.0
    assert r.json()["abandonment_rate"] == 0.0


@pytest.mark.asyncio
async def test_unknown_store_returns_404(client):
    r = await client.get("/stores/FAKE_STORE_999/metrics")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_reentry_same_visitor_id_one_unique_visitor(client):
    # unique_visitors counts distinct visitor_ids from ZONE_ENTER events (floor cameras).
    # Same visitor_id appearing twice (browse + re-browse) must count as 1.
    events = [
        make_event({"event_id": "e1", "event_type": "ENTRY",      "timestamp": "2026-04-10T06:45:00Z"}),
        make_event({"event_id": "e2", "event_type": "ZONE_ENTER",  "timestamp": "2026-04-10T06:46:00Z",
                    "zone_id": "SKINCARE_WALL"}),
        make_event({"event_id": "e3", "event_type": "ZONE_EXIT",   "timestamp": "2026-04-10T06:50:00Z",
                    "zone_id": "SKINCARE_WALL", "dwell_ms": 240000}),
        make_event({"event_id": "e4", "event_type": "EXIT",        "timestamp": "2026-04-10T07:00:00Z"}),
        make_event({"event_id": "e5", "event_type": "REENTRY",     "timestamp": "2026-04-10T07:10:00Z"}),
        make_event({"event_id": "e6", "event_type": "ZONE_ENTER",  "timestamp": "2026-04-10T07:11:00Z",
                    "zone_id": "SKINCARE_WALL"}),
    ]
    await client.post("/events/ingest", json={"events": events})
    r = await client.get("/stores/STORE_BLR_002/metrics")
    # Same visitor_id across both visits — must count as 1 unique visitor
    assert r.json()["unique_visitors"] == 1, "Re-entry must not inflate unique visitor count"