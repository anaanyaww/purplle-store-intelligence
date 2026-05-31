# PROMPT: "Test /anomalies: severity must be INFO/WARN/CRITICAL, each anomaly has suggested_action,
# queue spike triggers BILLING_QUEUE_SPIKE, dead zone detected, stale feed warning."
# CHANGES MADE: Made severity set explicit, added queue_depth threshold boundary test.

import pytest
from tests.conftest import make_event

VALID_SEVERITIES = {"INFO", "WARN", "CRITICAL"}


@pytest.mark.asyncio
async def test_all_severities_valid(client):
    r = await client.get("/stores/STORE_BLR_002/anomalies")
    for a in r.json()["anomalies"]:
        assert a["severity"] in VALID_SEVERITIES, f"Unknown severity: {a['severity']}"


@pytest.mark.asyncio
async def test_every_anomaly_has_suggested_action(client):
    r = await client.get("/stores/STORE_BLR_002/anomalies")
    for a in r.json()["anomalies"]:
        assert a.get("suggested_action"), f"Missing suggested_action for {a['anomaly_type']}"


@pytest.mark.asyncio
async def test_queue_spike_detected(client):
    billing = make_event({
        "event_id": "bq-001",
        "camera_id": "CAM_BILLING",
        "visitor_id": "VIS_q1",
        "event_type": "BILLING_QUEUE_JOIN",
        "timestamp": "2026-04-10T14:45:00Z",
        "zone_id": "CASH_COUNTER",
        "metadata": {"queue_depth": 5, "sku_zone": None, "session_seq": 3},
    })
    await client.post("/events/ingest", json={"events": [billing]})
    r = await client.get("/stores/STORE_BLR_002/anomalies")
    types = [a["anomaly_type"] for a in r.json()["anomalies"]]
    assert "BILLING_QUEUE_SPIKE" in types


@pytest.mark.asyncio
async def test_no_queue_spike_below_threshold(client):
    billing = make_event({
        "event_id": "bq-002",
        "camera_id": "CAM_BILLING",
        "visitor_id": "VIS_q2",
        "event_type": "BILLING_QUEUE_JOIN",
        "timestamp": "2026-04-10T14:45:00Z",
        "zone_id": "CASH_COUNTER",
        "metadata": {"queue_depth": 2, "sku_zone": None, "session_seq": 1},
    })
    await client.post("/events/ingest", json={"events": [billing]})
    r = await client.get("/stores/STORE_BLR_002/anomalies")
    types = [a["anomaly_type"] for a in r.json()["anomalies"]]
    assert "BILLING_QUEUE_SPIKE" not in types


@pytest.mark.asyncio
async def test_conversion_drop_fires_with_zero_rate(client):
    # With 0.0 conversion rate and seeded baseline of 0.35,
    # CONVERSION_DROP must always fire
    entry = {
        "event_id": "cd-001",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_ENTRY",
        "visitor_id": "VIS_cd1",
        "event_type": "ENTRY",
        "timestamp": "2026-04-10T06:45:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.9,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1}
    }
    await client.post("/events/ingest", json={"events": [entry]})
    r = await client.get("/stores/STORE_BLR_002/anomalies")
    types = [a["anomaly_type"] for a in r.json()["anomalies"]]
    assert "CONVERSION_DROP" in types, "CONVERSION_DROP must fire when rate is 0.0"


@pytest.mark.asyncio
async def test_health_endpoint_returns_valid_structure(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body
    assert body["status"] in ("OK", "DEGRADED", "DOWN")
    assert "stores" in body
    assert "warnings" in body
    assert isinstance(body["warnings"], list)