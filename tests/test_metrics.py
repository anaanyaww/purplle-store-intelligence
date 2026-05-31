# PROMPT: "Test /metrics, /funnel, /heatmap: empty store zeros, all-staff clip = 0 visitors,
# funnel stages in correct order, drop_off_pct 0-100, heatmap normalised_score 0-100."
# CHANGES MADE: Added heatmap score range check, all-staff test, funnel stage order assertion.

import pytest
from tests.conftest import make_event


@pytest.mark.asyncio
async def test_empty_store_all_zeros(client):
    r = await client.get("/stores/STORE_BLR_002/metrics")
    body = r.json()
    assert body["unique_visitors"] == 0
    assert body["conversion_rate"] == 0.0
    assert body["queue_depth"] == 0
    assert body["abandonment_rate"] == 0.0


@pytest.mark.asyncio
async def test_all_staff_clip_zero_visitors(client):
    for i in range(5):
        evt = make_event({"event_id": f"staff-{i}", "is_staff": True,
                          "visitor_id": f"VIS_staff_{i}"})
        await client.post("/events/ingest", json={"events": [evt]})
    r = await client.get("/stores/STORE_BLR_002/metrics")
    assert r.json()["unique_visitors"] == 0


@pytest.mark.asyncio
async def test_funnel_stages_correct_order(client):
    r = await client.get("/stores/STORE_BLR_002/funnel")
    stages = [s["stage"] for s in r.json()["stages"]]
    assert stages == ["Entry", "Zone Visit", "Billing Queue", "Purchase"]


@pytest.mark.asyncio
async def test_funnel_drop_off_pct_in_valid_range(client):
    r = await client.get("/stores/STORE_BLR_002/funnel")
    for s in r.json()["stages"]:
        assert 0.0 <= s["drop_off_pct"] <= 100.0, f"drop_off_pct out of range for {s['stage']}"


@pytest.mark.asyncio
async def test_heatmap_normalised_score_range(client):
    zone_evt = make_event({
        "event_id": "z1", "event_type": "ZONE_ENTER",
        "zone_id": "SKINCARE_WALL", "timestamp": "2026-04-10T07:00:00Z"
    })
    await client.post("/events/ingest", json={"events": [make_event(), zone_evt]})
    r = await client.get("/stores/STORE_BLR_002/heatmap")
    for z in r.json()["zones"]:
        assert 0.0 <= z["normalized_score"] <= 100.0
