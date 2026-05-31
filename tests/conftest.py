"""Shared test fixtures — fresh DB per test via environment variable."""
import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


def make_event(overrides=None):
    base = {
        "event_id": "evt-001",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_ENTRY",
        "visitor_id": "VIS_aaa",
        "event_type": "ENTRY",
        "timestamp": "2026-04-10T06:45:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.9,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    if overrides:
        base.update(overrides)
    return base


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setenv("POS_CSV_PATH", "data/pos_transactions.csv")

    # Patch DB_PATH on database module (source of truth)
    import app.database as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)

    # Init tables in temp DB
    await dbmod.init_db()

    # Now import app fresh each test via fresh ASGI transport
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
